"""`POST /api/v1/clauses/extract`, the clause-suggestion endpoint.

The endpoint shipped untested and with three defects (see the module docstring in
`backend/api/routers/extract.py`). These tests pin the repaired behaviour so the
replacement knowledge providers in spec 05 M12 have something to be checked against
before this module is deleted.

The endpoint uses its own session from `get_session`, so it cannot see rows written in a
test's rolled-back transaction. Contracts are therefore created through the real session
factory and deleted afterwards, as in `tests/test_api.py`.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

from backend.api.deps import get_session_factory
from backend.api.main import app
from backend.workspace.models import Contract, ExtractedClauseMatch, ExtractedDocument

EXTRACT = "/api/v1/clauses/extract"

#: Appears nowhere in the clause library, so no clause body can match it.
NONSENSE = "zorblax"


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def stored_contract() -> AsyncIterator[uuid.UUID]:
    """A contract committed through the real factory, so the endpoint can see it."""
    contract = Contract(id=uuid.uuid4(), contract_type="nda", request="Draft an NDA")
    factory = get_session_factory()
    async with factory() as s:
        s.add(contract)
        await s.commit()
    try:
        yield contract.id
    finally:
        async with factory() as s:
            await s.execute(delete(Contract).where(Contract.id == contract.id))
            await s.commit()


def _upload(text: str, name: str = "doc.txt") -> dict[str, tuple[str, bytes, str]]:
    return {"files": (name, text.encode("utf-8"), "text/plain")}


# ---------------------------------------------------------------------------- scoring


async def test_scores_are_clause_relative(client: AsyncClient) -> None:
    """A term in the document but in no clause matches nothing.

    The original scoring added the document's own term count to every clause's score
    identically. Being constant across the loop it never changed the ranking, but it did
    push every clause past the `score > 0` filter — so any prompt whose words appeared in
    the upload returned the entire clause library as "matches".
    """
    response = await client.post(
        EXTRACT,
        files=_upload(f"This document is about {NONSENSE} and nothing else."),
        data={"prompt": NONSENSE},
    )

    assert response.status_code == 200
    assert response.json()["matches"] == []


async def test_matches_clauses_whose_text_overlaps_the_prompt(client: AsyncClient) -> None:
    response = await client.post(
        EXTRACT,
        files=_upload("Any document text at all."),
        data={"prompt": "confidential information disclosure"},
    )

    assert response.status_code == 200
    matches = response.json()["matches"]
    assert matches, "a prompt matching clause bodies should return matches"
    assert any(m["clause_id"] == "nda.confidentiality" for m in matches)
    assert all(m["score"] > 0 for m in matches)


async def test_matches_are_ranked_best_first(client: AsyncClient) -> None:
    response = await client.post(
        EXTRACT,
        files=_upload("Any document text at all."),
        data={"prompt": "confidential information disclosure termination payment"},
    )

    scores = [m["score"] for m in response.json()["matches"]]
    assert scores == sorted(scores, reverse=True)


async def test_short_prompt_words_are_ignored(client: AsyncClient) -> None:
    """ "the", "and", "for" match everything and rank nothing."""
    response = await client.post(
        EXTRACT, files=_upload("Any document text."), data={"prompt": "the and for of a"}
    )

    assert response.json()["matches"] == []


# ------------------------------------------------------------------------ persistence


async def test_nothing_is_persisted_without_a_contract_id(client: AsyncClient) -> None:
    response = await client.post(
        EXTRACT, files=_upload("Confidential information."), data={"prompt": "confidential"}
    )
    assert response.status_code == 200

    async with get_session_factory()() as s:
        stored = (await s.execute(select(ExtractedDocument))).scalars().all()
    assert all(d.filename != "doc.txt" or d.content != "Confidential information." for d in stored)


async def test_document_and_matches_are_persisted_with_a_contract_id(
    client: AsyncClient, stored_contract: uuid.UUID
) -> None:
    response = await client.post(
        EXTRACT,
        files=_upload("Confidential information about disclosure."),
        data={"prompt": "confidential disclosure", "contract_id": str(stored_contract)},
    )
    assert response.status_code == 200

    async with get_session_factory()() as s:
        documents = (
            (
                await s.execute(
                    select(ExtractedDocument).where(
                        ExtractedDocument.contract_id == stored_contract
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(documents) == 1
        assert documents[0].content == "Confidential information about disclosure."

        matches = (
            (
                await s.execute(
                    select(ExtractedClauseMatch).where(
                        ExtractedClauseMatch.extracted_document_id == documents[0].id
                    )
                )
            )
            .scalars()
            .all()
        )
        assert matches, "matches returned to the client must also be recorded"


async def test_match_score_survives_the_round_trip_as_a_float(
    stored_contract: uuid.UUID,
) -> None:
    """`score` must not truncate.

    The column was `Integer` under a `Mapped[float]` annotation, so a 0.87 similarity was
    written as 0. Nothing produces fractional scores today — keyword counts are whole
    numbers — which is exactly why this needs an explicit test rather than waiting for
    the semantic ranker in M12 to discover it.
    """
    factory = get_session_factory()
    async with factory() as s:
        document = ExtractedDocument(
            id=uuid.uuid4(),
            contract_id=stored_contract,
            filename="d.txt",
            sha256="0" * 64,
            size_bytes=1,
            content="x",
        )
        s.add(document)
        await s.flush()
        s.add(
            ExtractedClauseMatch(
                id=uuid.uuid4(),
                extracted_document_id=document.id,
                clause_id="nda.confidentiality",
                score=0.87,
                snippet="x",
            )
        )
        await s.commit()

    async with factory() as s:
        stored = (
            (
                await s.execute(
                    select(ExtractedClauseMatch).where(
                        ExtractedClauseMatch.extracted_document_id == document.id
                    )
                )
            )
            .scalars()
            .one()
        )
        assert stored.score == 0.87


# --------------------------------------------------------------------------- refusals


async def test_unsupported_file_type_is_refused(client: AsyncClient) -> None:
    response = await client.post(
        EXTRACT,
        files={"files": ("payload.exe", b"MZ\x90\x00", "application/octet-stream")},
        data={"prompt": "confidential"},
    )
    assert response.status_code == 415


async def test_more_than_five_uploads_is_refused(client: AsyncClient) -> None:
    files = [("files", (f"doc{n}.txt", b"text", "text/plain")) for n in range(6)]
    response = await client.post(EXTRACT, files=files, data={"prompt": "confidential"})
    assert response.status_code == 422


async def test_an_empty_document_is_refused(client: AsyncClient) -> None:
    response = await client.post(EXTRACT, files=_upload("   "), data={"prompt": "confidential"})
    assert response.status_code == 422
