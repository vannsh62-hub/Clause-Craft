"""Suggest approved clauses for an uploaded document.

This endpoint is **advisory**. It ranks clauses from the approved library by keyword
overlap with the user's prompt so the intake UI can pre-select likely-relevant ones. It
does not decide anything: the drafting agent still retrieves and renders clauses through
`backend/tools/clause_tool.py`, and nothing here reaches a contract.

Scheduled for replacement by the TemplateProvider / ReferenceProvider knowledge
providers (spec 05 §5, milestone M12), at which point this module is deleted. Until then
it stays because `frontend/components/DocumentUpload.tsx` calls it.

Three defects were repaired in place rather than left for the rewrite:

- The semantic path called `openai.Embeddings.create`, an API removed in openai v1. It
  sat inside a bare `except`, so `use_semantic` flipped to False on the first request
  every time and *every* score was really a raw keyword count. Dead code that made the
  endpoint look smarter than it was; removed.
- The session came from `backend.core.database.get_session` while every other router
  uses `backend.api.deps.get_session`, so this endpoint opened a second engine and
  connection pool in the same process.
- Scores added the prompt-term count of the *document* to every clause identically. That
  is a constant across the loop, so it never changed the ranking, but it did push every
  clause in the library past the `score > 0` filter — which is why the endpoint returned
  the whole library on any prompt whose words appeared in the upload. Scoring is now
  clause-relative.
"""

from __future__ import annotations

import hashlib
import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from backend.api.deps import get_session
from backend.api.uploads import MAX_UPLOAD_BYTES, MAX_UPLOADS, extract_text, reject_unsupported
from backend.clauselib.loader import load_library
from backend.workspace.models import ExtractedClauseMatch, ExtractedDocument

router = APIRouter(prefix="/clauses", tags=["clauses"])

#: Prompt tokens at or below this length ("the", "and", "for") match everything and rank
#: nothing, so they are dropped before scoring.
MIN_TERM_LEN = 3

#: Characters of surrounding document text returned with each match.
SNIPPET_RADIUS = 80


class ClauseMatch(BaseModel):
    """One suggested clause, with the evidence for suggesting it."""

    clause_id: str
    score: float = Field(ge=0.0)
    snippet: str


class ExtractResponse(BaseModel):
    matches: list[ClauseMatch]


def _snippet(text: str, terms: list[str], fallback: str) -> str:
    """Return document text around the earliest matching term."""
    lowered = text.lower()
    positions = [pos for pos in (lowered.find(t) for t in terms) if pos >= 0]
    if not positions:
        return fallback[: SNIPPET_RADIUS * 2]
    start = min(positions)
    return text[max(0, start - SNIPPET_RADIUS) : start + SNIPPET_RADIUS]


@router.post("/extract", response_model=ExtractResponse)
async def extract_clauses(
    files: list[UploadFile] = File(...),
    prompt: str = Form(""),
    contract_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
) -> ExtractResponse:
    """Rank approved clauses by keyword overlap with `prompt`.

    Persists the uploaded text and its matches only when `contract_id` is supplied;
    without one this is a read-only suggestion call.
    """
    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    if len(files) > MAX_UPLOADS:
        raise HTTPException(
            status_code=422, detail=f"Upload up to {MAX_UPLOADS} reference documents."
        )

    terms = [t.lower() for t in prompt.split() if len(t) > MIN_TERM_LEN]
    clauses = load_library()
    matched: list[ClauseMatch] = []

    for upload in files:
        filename = upload.filename or "upload"
        reject_unsupported(filename)

        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{filename} exceeds the 10 MB limit.")
        try:
            text = extract_text(filename, data)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not read {filename}.") from exc
        if not text:
            raise HTTPException(status_code=422, detail=f"{filename} contains no extractable text.")

        document: ExtractedDocument | None = None
        if contract_id is not None:
            document = ExtractedDocument(
                id=uuid.uuid4(),
                contract_id=contract_id,
                filename=filename,
                sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
                size_bytes=len(text.encode("utf-8")),
                content=text,
            )
            session.add(document)
            # The match rows carry `extracted_document_id` as a plain value rather than
            # through a relationship, so the unit of work does not know they depend on
            # this row and is free to insert them first. Flush to fix the order.
            await session.flush()

        for clause in clauses:
            body = (getattr(clause, "body", "") or "").lower()
            # Clause-relative: only how well *this* clause matches the prompt.
            score = float(sum(body.count(term) for term in terms))
            if score <= 0:
                continue

            snippet = _snippet(text, terms, fallback=body)
            if document is not None:
                session.add(
                    ExtractedClauseMatch(
                        id=uuid.uuid4(),
                        extracted_document_id=document.id,
                        clause_id=clause.id,
                        score=score,
                        snippet=snippet,
                    )
                )
            matched.append(ClauseMatch(clause_id=clause.id, score=score, snippet=snippet))

    await session.commit()

    matched.sort(key=lambda m: m.score, reverse=True)
    return ExtractResponse(matches=matched)
