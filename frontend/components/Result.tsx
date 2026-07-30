"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { createExport, downloadUrl } from "@/lib/api";
import type { ContractVersion } from "@/lib/types";

const PASS_MARK = 90;

export function Result({ contractId, version, markdown, previewLoading, message }: { contractId: string; version: ContractVersion | null; markdown: string | null; previewLoading: boolean; message: string }) {
  const markdownComponents: Record<string, React.ComponentType<any>> = {
    h1: ({ node, ...props }) => <h1 className="doc-heading mt-0 text-2xl font-semibold tracking-tight" {...props} />,
    h2: ({ node, ...props }) => <h2 className="doc-heading mt-8 text-xl font-semibold tracking-tight" {...props} />,
    h3: ({ node, ...props }) => <h3 className="doc-heading mt-6 text-lg font-semibold tracking-tight" {...props} />,
    p: ({ node, ...props }) => <p className="doc-paragraph mt-4 leading-8 text-sm" {...props} />,
    strong: ({ node, ...props }) => <strong className="font-semibold" {...props} />,
    em: ({ node, ...props }) => <em className="italic" {...props} />,
    ul: ({ node, ...props }) => <ul className="doc-list list-disc pl-6 mt-4 space-y-2" {...props} />,
    ol: ({ node, ...props }) => <ol className="doc-list list-decimal pl-6 mt-4 space-y-2" {...props} />,
    li: ({ node, ordered, ...props }) => <li className="mt-2 leading-7" {...props} />,
    blockquote: ({ node, ...props }) => <blockquote className="doc-blockquote mt-6 rounded-3xl border-l-4 border-[color:var(--accent-soft)] bg-[rgba(15,118,110,0.05)] px-5 py-4 italic text-sm leading-7" {...props} />,
    code: ({ node, inline, className, children, ...props }) => inline ? <code className="doc-inline-code rounded-sm bg-[rgba(15,118,110,0.1)] px-1 py-0.5 text-sm" {...props}>{children}</code> : <pre className="doc-code my-6 overflow-auto rounded-3xl bg-[rgba(15,118,110,0.08)] p-5 text-sm leading-7" {...props}><code>{children}</code></pre>,
    a: ({ node, ...props }) => <a className="theme-link underline decoration-[color:var(--accent)] decoration-2 underline-offset-4" {...props} />,
  };

  const [downloading, setDownloading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const finalized = version?.finalized_at != null;
  const needsReview = version?.needs_human_review === true;

  async function download() {
    setDownloading(true); setError(null);
    try { const info = await createExport(contractId); window.location.href = downloadUrl(info.id); }
    catch (err) { setError(err instanceof Error ? err.message : String(err)); }
    finally { setDownloading(false); }
  }

  return <div className="space-y-5">
    {needsReview && <div className="warning-panel"><p className="text-sm font-semibold">A human must review this before it is signed.</p><p className="mt-1 text-sm leading-6">It contains every required clause but scored {version?.score}/100, below the pass mark of {PASS_MARK}.</p></div>}

    <section className="work-card">
      <div className="flex items-baseline justify-between gap-4"><div><p className="eyebrow">Draft complete</p><h2 className="mt-1 card-title">Review summary</h2></div>{version?.score != null && <span className="score-pill">{version.score}/100 / attempt {version.attempt}</span>}</div>
      <p className="mt-4 whitespace-pre-wrap text-sm leading-6 theme-muted">{message}</p>
    </section>

    {version && version.clause_ids.length > 0 && <section className="work-card"><h2 className="card-title">Clause provenance</h2><p className="mb-4 mt-1 muted-copy">Every clause below was reproduced from the approved library. Open one to inspect its source text.</p><ul className="grid gap-2 sm:grid-cols-2">{version.clause_ids.map((id) => <li key={id}><a className="clause-chip" href={`/api/v1/clauses/${id}/text`} target="_blank" rel="noreferrer">{id}</a></li>)}</ul></section>}

    <section className="work-card overflow-hidden p-0">
      <div className="flex items-center justify-between border-b border-[color:var(--border)] px-5 py-4 sm:px-6">
        <div>
          <p className="eyebrow">Document preview</p>
          <h2 className="mt-1 card-title">Contract</h2>
        </div>
        <span className="panel-badge">Read-only</span>
      </div>
      <div className="document-preview-shell bg-[color:var(--surface-muted)] p-6 sm:p-8">
        <div className="document-page mx-auto w-full max-w-5xl rounded-[32px] border border-[color:var(--border)] bg-[#fcfbf5] shadow-[0_30px_80px_-32px_rgba(15,23,42,0.24)]">
          <div className="document-page-content min-h-[40rem] px-10 py-10">
            {previewLoading && !markdown ? (
              <div className="document-preview-loading">
                <p className="text-sm font-semibold">Draft is loading…</p>
                <p className="mt-2 text-sm theme-muted">The document preview will appear once the draft is ready.</p>
              </div>
            ) : markdown ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
                {markdown}
              </ReactMarkdown>
            ) : (
              <div className="document-preview-loading">
                <p className="text-sm font-semibold">No draft preview available yet.</p>
                <p className="mt-2 text-sm theme-muted">Please wait until the draft finishes generating.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>

    {finalized && <section className="work-card final-note"><p className="text-xs leading-5 theme-muted">This document was assembled by an automated system from a library of counsel-approved clauses. It is a draft, not legal advice, and must not be executed until reviewed by a lawyer.</p><button onClick={download} disabled={downloading} className="primary-action mt-4 disabled:opacity-40">{downloading ? "Preparing..." : "Download .docx"}</button>{error && <p className="mt-2 text-sm text-rose-600">{error}</p>}</section>}
  </div>;
}
