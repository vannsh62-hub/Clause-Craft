from __future__ import annotations

import json
import re
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.datastructures import UploadFile

from backend.api.deps import get_session, get_session_factory
from backend.api.runner import launch_answer_run, launch_first_run, new_run
from backend.api.schemas import (
    AnswersRequest,
    ArtifactOut,
    ClauseActionOut,
    ClauseActionsRequest,
    ClauseActionsResponse,
    ClauseAnalyseRequest,
    ClauseAnalyseResponse,
    ClauseEditSuggestRequest,
    ClauseEditSuggestResponse,
    ClauseFillRequest,
    ClauseFillResponse,
    ContractOut,
    ContractSummaryOut,
    ContractVariablesResponse,
    ContractVariablesUpdate,
    DocumentSaveRequest,
    DocumentSaveResponse,
    DraftRequest,
    FileOut,
    ReplicateAccepted,
    RunAccepted,
    TodoOut,
    VersionOut,
)
from backend.api.uploads import (
    ALLOWED_UPLOADS,
    MAX_UPLOAD_BYTES,
    MAX_UPLOADS,
)
from backend.api.uploads import (
    extract_text as _extract_text,
)
from backend.artifacts import Artifact
from backend.clauselib.loader import ClauseLibraryError, get_clause
from backend.core.variable_aliases import resolve_variables
from backend.invariants.render import render_clause
from backend.schemas.errors import WorkspaceError
from backend.workspace.models import AgentTodo, Contract, ContractVersion, PendingQuestion
from backend.workspace.store import WorkspaceStore

router = APIRouter(prefix="/contracts", tags=["contracts"])


def _reference_path(filename: str, number: int) -> str:
    """Make an agent-readable, canonical workspace path from an untrusted upload name."""
    stem = filename.rsplit(".", 1)[0].lower()
    slug = re.sub(r"[^a-z0-9]+", "-", stem).strip("-") or "reference"
    return f"references/{number:02d}-{slug[:120]}.txt"


def _parse_clause_ids(field: UploadFile | str | None) -> list[str] | None:
    """Read client-suggested clause IDs from a form field.

    Accepts a JSON array or a comma-separated string, because the two clients in tree
    disagree. A field that is absent, blank, or a file upload yields None — these IDs are
    only a hint to the agent, so a malformed one is dropped rather than made a 422.
    """
    if not isinstance(field, str) or not field.strip():
        return None
    text = field.strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(parsed, list):
            return None
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [s.strip() for s in text.split(",") if s.strip()]


def _is_truthy(value: object) -> bool:
    return isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on"}


async def _request_and_uploads(
    request: Request,
) -> tuple[str, list[UploadFile], list[str] | None, bool]:
    """Accept JSON for API compatibility and multipart form data for the web intake flow.

    The fourth value is whether the first upload should be treated as a **template** to
    transform (Mode 2), rather than a reference document. It is a distinct knowledge path —
    the template's text is authoritative and its formatting must survive into the output —
    so the client states it explicitly.
    """
    content_type = request.headers.get("content-type", "")
    try:
        if content_type.startswith("application/json"):
            body = DraftRequest.model_validate(await request.json())
            return body.request, [], body.clause_ids, False
        if content_type.startswith("multipart/form-data"):
            form = await request.form()
            draft_request = DraftRequest(request=str(form.get("request", "")), clause_ids=None)
            uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
            if len(uploads) > MAX_UPLOADS:
                raise HTTPException(
                    status_code=422, detail=f"Upload up to {MAX_UPLOADS} reference documents."
                )
            clause_ids = _parse_clause_ids(form.get("clause_ids"))
            as_template = _is_truthy(form.get("as_template"))
            return draft_request.request, uploads, clause_ids, as_template
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc
    raise HTTPException(status_code=415, detail="Send application/json or multipart/form-data.")


async def _get_contract(session: AsyncSession, contract_id: uuid.UUID) -> Contract:
    contract = await session.get(Contract, contract_id)
    if contract is None:
        raise HTTPException(status_code=404, detail="no such contract")
    return contract


@router.get("", response_model=list[ContractSummaryOut])
async def list_contracts(
    session: AsyncSession = Depends(get_session),
) -> list[ContractSummaryOut]:
    """Recent contracts, newest first — the assistant history and the projects list.

    A contract with a finalized draft is a "project"; the rest are in-progress conversations.
    Both come from here, and the client splits them.
    """
    rows = (
        (await session.execute(select(Contract).order_by(Contract.created_at.desc()).limit(50)))
        .scalars()
        .all()
    )
    finalized_ids = set(
        (
            await session.execute(
                select(ContractVersion.contract_id).where(ContractVersion.finalized_at.is_not(None))
            )
        )
        .scalars()
        .all()
    )
    return [
        ContractSummaryOut(
            id=c.id,
            request=c.request,
            contract_type=c.contract_type,
            status=c.status,
            created_at=c.created_at,
            finalized=c.id in finalized_ids,
        )
        for c in rows
    ]


MAX_COPIES = 5

#: What a replicated contract's agent is told. Each copy is an independent draft off the same
#: source, which is the point: three variants to compare, not one document edited three times.
_TEMPLATE_BRIEF = (
    "\n\nThe user uploaded a document to use as a TEMPLATE to transform. Its structure "
    "and formatting must be preserved; adapt its terms to the request rather than "
    "drafting a new document from scratch."
)


async def _seed_template_contract(
    draft_request: str, data: bytes, filename: str, session: AsyncSession
) -> RunAccepted:
    """Create one contract seeded with a byte-identical copy of `data`, and start its run."""
    contract = Contract(request=draft_request, status="planning")
    session.add(contract)
    await session.commit()

    from backend.core.run_context import RunContext
    from backend.knowledge.providers.template import store_template

    ctx = RunContext(contract_id=contract.id, session_factory=get_session_factory())
    try:
        await store_template(data, filename, ctx)
    except Exception as exc:
        await session.delete(contract)
        await session.commit()
        raise HTTPException(
            status_code=422, detail=f"Could not use {filename} as a template: {exc}"
        ) from exc

    run_id = await new_run(contract.id)
    launch_first_run(contract.id, run_id, draft_request + _TEMPLATE_BRIEF)
    return RunAccepted(
        contract_id=contract.id, run_id=run_id, events=f"/api/v1/runs/{run_id}/events"
    )


@router.post("/replicate", status_code=status.HTTP_202_ACCEPTED, response_model=ReplicateAccepted)
async def replicate_contract(
    request: Request, session: AsyncSession = Depends(get_session)
) -> ReplicateAccepted:
    """Start several independent drafts from one uploaded document.

    "Use this NDA as a template and give me three drafts I can adapt." Each copy gets the
    **same bytes** — the upload is read once and stored per contract without re-encoding — so
    the copies are indistinguishable at the source and diverge only in what each run drafts.

    Deliberately separate from `POST /contracts` rather than a `copies` parameter on it: that
    endpoint's contract is "one request, one run, one id", and returning a list from it would
    break every existing caller.
    """
    if not request.headers.get("content-type", "").startswith("multipart/form-data"):
        raise HTTPException(status_code=415, detail="Send multipart/form-data with a .docx file.")

    form = await request.form()
    draft_request = str(form.get("request", "")).strip()
    if not draft_request:
        raise HTTPException(status_code=422, detail="Describe what each copy should become.")

    uploads = [item for item in form.getlist("files") if isinstance(item, UploadFile)]
    if not uploads:
        raise HTTPException(status_code=422, detail="Attach the document to replicate.")

    try:
        copies = int(str(form.get("copies", "1")))
    except ValueError:
        copies = 1
    if not 1 <= copies <= MAX_COPIES:
        raise HTTPException(status_code=422, detail=f"Ask for between 1 and {MAX_COPIES} copies.")

    filename = uploads[0].filename or "template.docx"
    if not filename.lower().endswith(".docx"):
        raise HTTPException(status_code=415, detail="A template must be a .docx file.")

    data = await uploads[0].read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail=f"{filename} exceeds the 10 MB limit.")

    accepted = [
        await _seed_template_contract(draft_request, data, filename, session) for _ in range(copies)
    ]
    return ReplicateAccepted(contracts=accepted)


@router.post("", status_code=status.HTTP_202_ACCEPTED, response_model=RunAccepted)
async def create_contract(
    request: Request, session: AsyncSession = Depends(get_session)
) -> RunAccepted:
    """Start a drafting run. Returns immediately; watch the SSE stream for progress."""
    draft_request, uploads, clause_ids, as_template = await _request_and_uploads(request)
    contract = Contract(request=draft_request, status="planning")
    session.add(contract)
    await session.flush()

    # A template is a distinct knowledge path (Mode 2): the first upload is the document to
    # transform, and the rest are references. Read its bytes now; store it after the contract
    # commits, because `store_template` writes through its own session.
    template_bytes: bytes | None = None
    template_filename = ""
    reference_uploads = uploads
    if as_template and uploads:
        head = uploads[0]
        reference_uploads = uploads[1:]
        template_filename = head.filename or "template.docx"
        if not template_filename.lower().endswith(".docx"):
            raise HTTPException(status_code=415, detail="A template must be a .docx file.")
        template_bytes = await head.read(MAX_UPLOAD_BYTES + 1)
        if len(template_bytes) > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413, detail=f"{template_filename} exceeds the 10 MB limit."
            )

    reference_paths: list[str] = []
    for number, upload in enumerate(reference_uploads, start=1):
        filename = upload.filename or f"reference-{number}.txt"
        suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        if suffix not in ALLOWED_UPLOADS:
            raise HTTPException(
                status_code=415, detail="Only TXT, Markdown, DOCX, and PDF files are supported."
            )
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{filename} exceeds the 10 MB limit.")
        try:
            text = _extract_text(filename, data)
        except HTTPException:
            raise
        except Exception as exc:
            raise HTTPException(status_code=422, detail=f"Could not read {filename}.") from exc
        if not text:
            raise HTTPException(status_code=422, detail=f"{filename} contains no extractable text.")
        path = _reference_path(filename, number)
        await WorkspaceStore(session).write(contract.id, path, text)
        reference_paths.append(path)

    await session.commit()

    # Store the template after commit so its own session sees the contract row. A DOCX that
    # the parser refuses (tracked changes, a corrupt package) fails the request cleanly, and
    # the just-created contract is removed so no empty run is left behind.
    if template_bytes is not None:
        from backend.core.run_context import RunContext
        from backend.knowledge.providers.template import store_template

        ctx = RunContext(contract_id=contract.id, session_factory=get_session_factory())
        try:
            await store_template(template_bytes, template_filename, ctx)
        except HTTPException:
            raise
        except Exception as exc:
            await session.delete(contract)
            await session.commit()
            raise HTTPException(
                status_code=422,
                detail=f"Could not use {template_filename} as a template: {exc}",
            ) from exc

    run_id = await new_run(contract.id)
    agent_request = draft_request
    if template_bytes is not None:
        agent_request += (
            "\n\nThe user uploaded a document to use as a TEMPLATE to transform. Its structure "
            "and formatting must be preserved; adapt its terms to the request rather than "
            "drafting a new document from scratch."
        )
    if reference_paths:
        agent_request += (
            "\n\nThe user provided reference documents. Before planning, inspect and use these "
            f"workspace files as source material: {', '.join(reference_paths)}."
        )
        # If the client suggested clause IDs, surface them to the agent as a preference.
        if clause_ids:
            agent_request += (
                "\n\nUser suggested relevant clause IDs: "
                + ", ".join(clause_ids)
                + ". The agent should consider these when selecting clauses."
            )
    launch_first_run(contract.id, run_id, agent_request)

    return RunAccepted(
        contract_id=contract.id, run_id=run_id, events=f"/api/v1/runs/{run_id}/events"
    )


@router.post("/{contract_id}/answers", status_code=status.HTTP_202_ACCEPTED)
async def answer(
    contract_id: uuid.UUID,
    body: AnswersRequest,
    session: AsyncSession = Depends(get_session),
) -> RunAccepted:
    """Answer the questions the agent asked, and resume the run."""
    contract = await _get_contract(session, contract_id)
    if contract.status != "awaiting_input":
        raise HTTPException(
            status_code=409, detail=f"contract is {contract.status}, not awaiting input"
        )

    pending = (
        (
            await session.execute(
                select(PendingQuestion).where(
                    PendingQuestion.contract_id == contract_id,
                    PendingQuestion.answered_at.is_(None),
                )
            )
        )
        .scalars()
        .all()
    )
    if not pending:
        raise HTTPException(status_code=409, detail="no outstanding questions")

    run_id = await new_run(contract_id)
    launch_answer_run(contract_id, run_id, body.answers)

    return RunAccepted(
        contract_id=contract_id, run_id=run_id, events=f"/api/v1/runs/{run_id}/events"
    )


@router.get("/{contract_id}", response_model=ContractOut)
async def get_contract(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> ContractOut:
    contract = await _get_contract(session, contract_id)
    # Self-heals the same way `/render` and `/clauses/analyse` do, so a value stored under a
    # near-miss name (or before the alias table existed) still comes back under its canonical
    # key — the frontend's Variable Memory is only as good as what it's handed here.
    return ContractOut.model_validate(contract, from_attributes=True).model_copy(
        update={"variables": resolve_variables(contract.variables or {})}
    )


@router.get("/{contract_id}/versions", response_model=list[VersionOut])
async def get_versions(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[ContractVersion]:
    await _get_contract(session, contract_id)
    rows = (
        (
            await session.execute(
                select(ContractVersion)
                .where(ContractVersion.contract_id == contract_id)
                .order_by(ContractVersion.attempt)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{contract_id}/document/save", response_model=DocumentSaveResponse)
async def save_document(
    contract_id: uuid.UUID,
    body: DocumentSaveRequest,
    session: AsyncSession = Depends(get_session),
) -> DocumentSaveResponse:
    """Persist the Document Preview/Editing view's current markdown.

    Manual WYSIWYG edits and applied assistant clause actions (insert/replace/remove/fill)
    previously lived only in the browser's React state — a reload, or `/export`, silently
    reverted to whatever the drafting engine last produced. This is the missing write path:

    * `final.md` in the workspace is overwritten, since that's what the preview reloads from
      (see the frontend's `loadPreview` effect) — written with the ordinary `write()` method,
      not `put_clause()`, because this is an editor-authored file, not approved clause text.
    * The finalized `ContractVersion.markdown` is updated too, since `/export` reads *that*
      column (not the workspace) when regenerating a docx.
    * `docx_storage_key` is cleared on that version when set, so a stale template-mode docx
      blob is never served after the markdown has diverged from it — `/export` falls back to
      regenerating from `markdown` once the key is gone.

    Requires a finalized version: there is nothing to persist edits *onto* before finalize has
    picked a winning draft.
    """
    await _get_contract(session, contract_id)

    version = (
        await session.execute(
            select(ContractVersion).where(
                ContractVersion.contract_id == contract_id,
                ContractVersion.finalized_at.is_not(None),
            )
        )
    ).scalar_one_or_none()
    if version is None:
        raise HTTPException(
            status_code=409,
            detail="no finalized version; nothing to save the document onto yet",
        )

    try:
        info = await WorkspaceStore(session).write(contract_id, "final.md", body.markdown)
    except WorkspaceError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    version.markdown = body.markdown
    if version.docx_storage_key:
        version.docx_storage_key = None

    await session.commit()

    return DocumentSaveResponse(path=info.path, version_id=version.id, updated_at=info.updated_at)


@router.get("/{contract_id}/workspace", response_model=list[FileOut])
async def get_workspace(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[FileOut]:
    await _get_contract(session, contract_id)
    files = await WorkspaceStore(session).ls(contract_id)
    return [FileOut(**f.model_dump()) for f in files]


@router.get("/{contract_id}/workspace/{path:path}")
async def read_workspace_file(
    contract_id: uuid.UUID, path: str, session: AsyncSession = Depends(get_session)
) -> dict[str, str]:
    await _get_contract(session, contract_id)

    try:
        content = await WorkspaceStore(session).read(contract_id, path)
    except WorkspaceError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"path": path, "content": content}


@router.get("/{contract_id}/artifacts", response_model=list[ArtifactOut])
async def get_artifacts(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[ArtifactOut]:
    """The pipeline's named artifacts and whether each has been produced.

    A typed catalogue over the raw workspace listing: a UI can show "Intent ✓, CKO ✓,
    Transformation Plan ✓" and link each to its content, which the layout — one canonical
    path per artifact — makes possible. The bytes themselves come from the existing
    `/workspace/{path}` endpoint.
    """
    await _get_contract(session, contract_id)
    store = WorkspaceStore(session)
    present = {f.path for f in await store.ls(contract_id)}
    return [ArtifactOut(name=a.name, path=a.path, present=a.path in present) for a in Artifact]


@router.get("/{contract_id}/todos", response_model=list[TodoOut])
async def get_todos(
    contract_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> list[AgentTodo]:
    await _get_contract(session, contract_id)
    rows = (
        (
            await session.execute(
                select(AgentTodo)
                .where(AgentTodo.contract_id == contract_id)
                .order_by(AgentTodo.seq)
            )
        )
        .scalars()
        .all()
    )
    return list(rows)


@router.post("/{contract_id}/clauses/{clause_id}/render")
async def render_clause_for_contract(
    contract_id: uuid.UUID, clause_id: str, session: AsyncSession = Depends(get_session)
) -> dict[str, object]:
    """Render an approved clause using the values already known about this contract.

    This is what powers "insert clause" from the document preview: `disclosing_party`,
    `receiving_party`, dates and the like come from `Contract.variables` — collected during
    drafting — so the user is never asked to retype them. Any variable this contract genuinely
    never resolved is left as its own `{{ name }}` placeholder rather than rendered blank, so a
    truly missing value stays visible instead of silently disappearing.
    """
    contract = await _get_contract(session, contract_id)

    try:
        clause = get_clause(clause_id)
    except ClauseLibraryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

    # re-applies the alias table + term_end_date derivation on top of whatever's stored —
    # self-heals contracts whose variables were recorded under a near-miss name or before
    # this mapping existed, without needing a re-run.
    known = resolve_variables(contract.variables or {})
    missing = sorted(v for v in clause.variables if v not in known or not known[v])
    values = {**known, **{name: f"{{{{ {name} }}}}" for name in missing}}

    rendered = render_clause(clause, values)
    return {
        "id": rendered.clause_id,
        "version": rendered.version,
        "title": rendered.title,
        "text": rendered.text,
        "missing": missing,
    }


@router.post("/{contract_id}/clauses/analyse", response_model=ClauseAnalyseResponse)
async def analyse_clause_fields(
    contract_id: uuid.UUID,
    body: ClauseAnalyseRequest,
    session: AsyncSession = Depends(get_session),
) -> ClauseAnalyseResponse:
    """Split the Fill-details modal's placeholder fields into known vs. genuinely missing.

    Deterministic and read-only: it never mutates the clause library or the contract, and
    never calls a model. `known` is looked up the same way `/render` looks up clause
    variables — through the alias table, self-healing near-miss names — so a field the
    contract already resolved during drafting is prefilled instead of asked for again.
    """
    contract = await _get_contract(session, contract_id)
    known_all = resolve_variables(contract.variables or {})
    known = {name: known_all[name] for name in body.fields if known_all.get(name)}
    missing = [name for name in body.fields if name not in known]
    return ClauseAnalyseResponse(known=known, missing=missing)


@router.patch("/{contract_id}/variables", response_model=ContractVariablesResponse)
async def update_contract_variables(
    contract_id: uuid.UUID,
    body: ContractVariablesUpdate,
    session: AsyncSession = Depends(get_session),
) -> ContractVariablesResponse:
    """Persist newly-confirmed placeholder values into the Contract Variable Memory.

    This is the write side of `/clauses/analyse`: once the Fill-details modal (or an Insert /
    AI-edit flow that resolved a placeholder) has a value the user just typed, it's saved here
    so no later clause in this contract ever asks for it again. Values are merged through the
    same `resolve_variables` alias table `/render` and `/analyse` read through, so a name typed
    as "Client Name" lands under the same canonical key a template's `{{ client_name }}` looks
    up — one write path, no separate normalization to keep in sync.

    A value already stored for a key is overwritten — the user is explicitly updating it (e.g.
    correcting "ABC Pvt Ltd" to "XYZ Pvt Ltd"); this endpoint doesn't second-guess that, the
    frontend decides whether to confirm "update everywhere" before calling it.
    """
    contract = await _get_contract(session, contract_id)
    normalized = resolve_variables(body.values)
    merged = {**(contract.variables or {}), **normalized}
    contract.variables = merged
    await session.commit()
    return ContractVariablesResponse(variables=resolve_variables(merged))


#: A fill-suggestion call this size would be a bug in the caller, not a legitimate ask —
#: the modal only ever asks about one clause's own placeholders.
MAX_FILL_FIELDS = 50


@router.post("/{contract_id}/clauses/fill", response_model=ClauseFillResponse)
async def fill_clause_fields(
    contract_id: uuid.UUID,
    body: ClauseFillRequest,
    session: AsyncSession = Depends(get_session),
) -> ClauseFillResponse:
    """Ask the assistant to suggest values for fields `analyse` could not resolve.

    This is the "Ask assistant" action in the Fill-details modal. It never writes to the
    clause library or the contract's stored variables — it returns suggestions for the
    modal's own editable inputs, which the user still applies (or edits, or ignores)
    themselves. `render_clause`/`invariants` remain the only path that substitutes text
    into a clause.
    """
    if len(body.fields) > MAX_FILL_FIELDS:
        raise HTTPException(
            status_code=422, detail=f"Ask about at most {MAX_FILL_FIELDS} fields at a time."
        )
    contract = await _get_contract(session, contract_id)

    from backend.core.run_context import RunContext
    from backend.subagents.fill_details.fill_agent import suggest_fill_values

    ctx = RunContext(
        contract_id=contract.id,
        session_factory=get_session_factory(),
        contract_type=contract.contract_type,
        jurisdiction=contract.jurisdiction,
    )
    try:
        result = await suggest_fill_values(body.clause_text, body.fields, ctx)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not get suggestions: {exc}"
        ) from exc

    suggestions = {pair.name: pair.value for pair in result.suggestions if pair.name in body.fields}
    unresolved = [name for name in body.fields if name not in suggestions]
    return ClauseFillResponse(suggestions=suggestions, unresolved=unresolved)


#: An instruction/clause this size would be a bug in the caller — the popover only ever
#: edits one clause at a time.
MAX_CLAUSE_EDIT_MARKDOWN = 20_000


@router.post("/{contract_id}/clauses/edit/suggest", response_model=ClauseEditSuggestResponse)
async def suggest_clause_edit_for_contract(
    contract_id: uuid.UUID,
    body: ClauseEditSuggestRequest,
    session: AsyncSession = Depends(get_session),
) -> ClauseEditSuggestResponse:
    """The Edit popover's ✨ AI Assistant action: suggest a rewrite of one clause.

    Sends only the single clause's own markdown to the agent — never the rest of the
    document — and never applies anything. The caller shows the suggestion in a
    side-by-side preview; splicing an approved rewrite back into the document happens
    client-side, the same way a manual edit does.
    """
    contract = await _get_contract(session, contract_id)

    from backend.core.run_context import RunContext
    from backend.subagents.clause_edit.clause_edit_agent import suggest_clause_edit

    ctx = RunContext(
        contract_id=contract.id,
        session_factory=get_session_factory(),
        contract_type=contract.contract_type,
        jurisdiction=contract.jurisdiction,
    )
    try:
        result = await suggest_clause_edit(body.clause_markdown, body.instruction, ctx)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not get a suggestion: {exc}"
        ) from exc

    return ClauseEditSuggestResponse(updated_clause=result.updated_clause, summary=result.summary)


@router.post("/{contract_id}/clauses/assistant", response_model=ClauseActionsResponse)
async def propose_clause_actions_for_contract(
    contract_id: uuid.UUID,
    body: ClauseActionsRequest,
    session: AsyncSession = Depends(get_session),
) -> ClauseActionsResponse:
    """One turn of the assistant panel: propose clause edits from a chat message.

    Returns proposals only — inserting a clause, replacing/removing one, or filling its
    fields all happen client-side afterwards, through the same deterministic functions
    the manual editor uses (`frontend/lib/clauses.ts`), once the user reviews and applies
    each one. This endpoint never writes to the contract, the workspace, or the clause
    library, and the model never authors clause text.
    """
    contract = await _get_contract(session, contract_id)

    from backend.core.run_context import RunContext
    from backend.subagents.clause_actions.clause_actions_agent import propose_clause_actions

    ctx = RunContext(
        contract_id=contract.id,
        session_factory=get_session_factory(),
        contract_type=contract.contract_type,
        jurisdiction=contract.jurisdiction,
    )
    try:
        history = [(turn.message, turn.reply) for turn in body.history]
        result = await propose_clause_actions(body.document, body.message, ctx, history=history)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Could not reach the assistant: {exc}"
        ) from exc

    actions = [
        ClauseActionOut(
            action=a.action,
            clause_title=a.clause_title,
            clause_id=a.clause_id,
            after_clause_title=a.after_clause_title,
            fields={pair.name: pair.value for pair in a.fields},
            reason=a.reason,
        )
        for a in result.actions
        if a.action in {"insert", "replace", "remove", "fill"}
    ]
    return ClauseActionsResponse(reply=result.reply, actions=actions)