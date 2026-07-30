# AI Contract Drafting Platform

A deep agent that drafts contracts from a library of counsel-approved clauses. It plans before
it writes, asks for information rather than inventing it, retrieves approved clause text instead
of generating it, scores its own draft, and revises.

> **The agent is autonomous over *process*. The tools are authoritative over *correctness*.**

It cannot emit a contract that is missing a required clause, contains an unresolved placeholder,
or reworded approved text — not because the prompt says so, but because the only path to a
document runs through `finalize_contract()`, which runs those checks in code and returns findings
instead of a contract.

## Run locally

```bash
docker compose up -d postgres
python -m venv .venv && .venv/bin/pip install -e ".[dev]"
echo "OPENAI_API_KEY=sk-..." > .env
.venv/bin/alembic upgrade head
.venv/bin/uvicorn backend.api.main:app --port 8000 --loop backend.core.event_loop:selector_loop_factory  # /docs
```

```bash
cd frontend
npm install
BACKEND_URL=http://localhost:8000 npm run build         # baked in at BUILD time
npm start                                               # http://localhost:3000
```

## Production deployment with Docker

Docker Compose runs PostgreSQL, FastAPI, and the Next.js UI as one private network. Only the UI
is published on port `3000` (or `APP_PORT`); it proxies `/api` requests to FastAPI internally.

```bash
cp .env.example .env
# Edit .env and set OPENAI_API_KEY and a long POSTGRES_PASSWORD.
docker compose up --build -d
docker compose ps
```

Open `http://YOUR_SERVER:3000`. The first backend start automatically applies the Alembic
migrations. Generated documents and the database live in named Docker volumes and survive
container rebuilds. For a public deployment, place a TLS reverse proxy (Caddy, Nginx, or your
hosting platform's HTTPS router) in front of port 3000; do not expose Postgres or port 8000.

Useful operational commands:

```bash
docker compose logs -f backend
docker compose up --build -d       # rebuild and replace services after an update
docker compose down                 # keeps database and generated-document volumes
```

Ask it for *"an NDA between ABC Pvt Ltd and XYZ Pvt Ltd under Indian law, courts at Mumbai"*.
It will write a plan, ask you for the effective date and the signatories, draft, judge, finalize,
and hand you a `.docx` in which every clause traces back to the library.

Ask it for an employment agreement and it will decline. There is no approved clause set, and a
tool that will confidently draft anything is a liability.

## Test it

```bash
.venv/bin/pytest -m "not requires_api_key"   # 332 tests, no network, no tokens
.venv/bin/pytest -m requires_api_key         # 3 live tests, real models
```

The first command exercises the entire correctness path: clause rendering, all three validation
gates, the workspace, the ledger, suspend/resume, the choke point, DOCX bytes, and the HTTP and
SSE surfaces. A `FakeModel` drives the real agent machinery, so none of it costs anything.

## Read it

| | |
|---|---|
| [docs/PRD.md](docs/PRD.md) | What it is for, who uses it, what it refuses to do |
| [docs/TDD.md](docs/TDD.md) | How it works, and where the original design was wrong |
| [docs/ROADMAP.md](docs/ROADMAP.md) | What shipped, what is unmeasured, what is next |
| [.claude/specs/01-contract-drafting-mvp.md](.claude/specs/01-contract-drafting-mvp.md) | The implementation spec |

## The shape of it

```
backend/invariants/   correctness. imports neither `agents` nor `openai` — asserted by a test
backend/tools/        thin @function_tool adapters over the invariants
backend/subagents/    orchestrator + drafting and judge sub-agents
backend/workspace/    a per-contract virtual filesystem in Postgres
clauses/              the approved library. git is the audit trail
```

Two things that look like details and are not. `backend/agents/` must never exist: the SDK's
import root is `agents`, and a local package of that name shadows it the moment `backend/`
reaches `sys.path`. And `backend/invariants/` must never import the SDK, so that nobody can
quietly put a model inside a correctness gate.
