# Contract Drafting — UI

A deliberately small client. It does four things: takes a request, shows the agent's plan as it
revises it, asks the questions the agent refused to guess at, and hands over the document.

```bash
npm install
npm run typecheck
BACKEND_URL=http://localhost:8000 npm run build
npm start                       # http://localhost:3000
```

For development, `npm run dev` reads `BACKEND_URL` from the environment on each start.

## Two things worth knowing

**`BACKEND_URL` is baked in at build time.** Next serialises the resolved rewrite into
`.next/routes-manifest.json`, so setting it for `next start` does nothing. If the API 404s with
a JSON body you did not write, you are talking to whatever else is listening on port 8000.

**The event stream is read by hand, not by `EventSource`.** `EventSource` reconnects on its own
and would reconnect at `?seq=0`, replaying the whole run and duplicating everything already on
screen. `lib/api.ts` tracks the last `seq` it saw and reconnects from exactly there, which is
what the server's replay endpoint exists for.

## What the screens mean

- **Plan** — written by the agent before it starts, and rewritten whenever the facts change.
  This is the deep-agent property made visible.
- **Activity** — every tool call, and its result when it returns. A pulsing dot is a call still
  in flight.
- **Questions** — the agent asked rather than guessed. Nothing is drafted until they are
  answered; the backend refuses to render a clause with a blank party name.
- **A human must review this** — a banner, not a badge. It means the draft passed every
  deterministic gate but scored below the pass mark. It is real; it is not approved.

The download button only appears for a finalized contract, and the disclaimer sits above it —
not only inside the file.
