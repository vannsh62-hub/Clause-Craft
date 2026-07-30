from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.api.schemas import ClauseOut, ClauseWrite
from backend.clauselib.loader import (
    ClauseLibraryError,
    clauses_for,
    contract_types,
    get_clause,
    load_library,
)
from backend.clauselib.writer import delete_clause, upsert_clause
from backend.schemas.clause import Clause

router = APIRouter(prefix="/clauses", tags=["clauses"])


def _country(clause: Clause) -> str:
    """The jurisdictions rendered for the library view.

    A single jurisdiction shows as itself (`IN`); a clause that applies everywhere shows as
    `Global`, which is what "no restriction" means to a reader.
    """
    if not clause.jurisdictions or len(clause.jurisdictions) > 1:
        return "Global"
    return clause.jurisdictions[0]


def _to_out(clause: Clause) -> ClauseOut:
    return ClauseOut(
        id=clause.id,
        version=clause.version,
        title=clause.title,
        contract_type=clause.contract_types[0],
        required=clause.required,
        order=clause.order,
        variables=list(clause.variables),
        category=clause.title,
        country=_country(clause),
        risk=clause.risk,
        status="Approved",
        body=clause.body,
    )


@router.get("", response_model=list[ClauseOut])
async def list_clauses(contract_type: str | None = Query(None)) -> list[ClauseOut]:
    """Browse the approved library. Every clause a contract can contain is here, and only here."""
    if contract_type is None:
        return [_to_out(c) for c in load_library()]

    if contract_type not in contract_types():
        raise HTTPException(
            status_code=404,
            detail=f"no approved clause set for {contract_type!r}; "
            f"known types: {sorted(contract_types())}",
        )
    return [_to_out(c) for c in clauses_for(contract_type)]


@router.get("/{clause_id}/text")
async def clause_text(clause_id: str) -> dict[str, object]:
    """The raw approved template, variables unsubstituted."""
    try:
        clause = get_clause(clause_id)
    except ClauseLibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {
        "id": clause.id,
        "version": clause.version,
        "title": clause.title,
        "body": clause.body,
        "variables": list(clause.variables),
    }


@router.post("", response_model=ClauseOut, status_code=201)
async def create_clause(body: ClauseWrite) -> ClauseOut:
    """Add a clause to the library.

    Validated by the same loader the pipeline uses: a clause that would not load — a bad id,
    a body whose variables cannot be reconciled — is refused with the reason, so the library
    on disk is always one the drafting engine can read.
    """
    if _exists(body.id):
        raise HTTPException(status_code=409, detail=f"clause {body.id!r} already exists")
    return _write(body)


@router.put("/{clause_id}", response_model=ClauseOut)
async def update_clause(clause_id: str, body: ClauseWrite) -> ClauseOut:
    """Edit a clause. Editing bumps its version, so the library records that it changed."""
    if not _exists(clause_id):
        raise HTTPException(status_code=404, detail=f"no clause {clause_id!r}")
    if body.id != clause_id:
        raise HTTPException(status_code=422, detail="a clause id cannot be changed by editing")
    return _write(body)


@router.delete("/{clause_id}", status_code=204)
async def remove_clause(clause_id: str) -> None:
    """Remove a clause from the library."""
    try:
        delete_clause(clause_id)
    except ClauseLibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _exists(clause_id: str) -> bool:
    try:
        get_clause(clause_id)
        return True
    except ClauseLibraryError:
        return False


def _write(body: ClauseWrite) -> ClauseOut:
    try:
        clause = upsert_clause(
            clause_id=body.id,
            title=body.title,
            contract_type=body.contract_type,
            jurisdiction=body.country,
            required=body.required,
            order=body.order,
            risk=body.risk,
            body=body.body,
        )
    except ClauseLibraryError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _to_out(clause)
