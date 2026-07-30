"use client";

import { useId, useRef, useState } from "react";

const MAX_FILES = 5;
const MAX_BYTES = 10 * 1024 * 1024;
const ACCEPTED = new Set(["text/plain", "text/markdown", "application/pdf", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]);

function valid(file: File) {
  return ACCEPTED.has(file.type) || /\.(txt|md|pdf|docx)$/i.test(file.name);
}

export function DocumentUpload({ files, onChange, disabled, onAnalyze, onAnalyzeResult }: { files: File[]; onChange: (files: File[]) => void; disabled: boolean; onAnalyze?: (files: File[]) => Promise<any>; onAnalyzeResult?: (res: any) => void }) {
  const inputId = useId();
  const input = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function add(incoming: FileList | File[]) {
    const candidates = Array.from(incoming);
    if (candidates.some((file) => !valid(file))) return setError("Use PDF, DOCX, TXT, or Markdown files.");
    if (candidates.some((file) => file.size > MAX_BYTES)) return setError("Each document must be 10 MB or smaller.");
    const next = [...files, ...candidates].slice(0, MAX_FILES);
    setError(files.length + candidates.length > MAX_FILES ? `You can attach up to ${MAX_FILES} documents.` : null);
    onChange(next);
  }

  return <div className="mt-6">
    <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1"><label className="text-sm font-semibold" htmlFor={inputId}>Reference documents</label><span className="theme-muted text-xs">Optional / PDF, DOCX, TXT, MD / 10 MB each</span></div>
    <div className={`upload-dropzone ${dragging ? "upload-dropzone-active" : ""} ${disabled ? "opacity-60" : ""}`} onDragOver={(event) => { event.preventDefault(); if (!disabled) setDragging(true); }} onDragLeave={() => setDragging(false)} onDrop={(event) => { event.preventDefault(); setDragging(false); if (!disabled) add(event.dataTransfer.files); }}>
      <div className="upload-icon" aria-hidden="true">↑</div>
      <p className="text-sm font-medium">Drop documents here, or <button type="button" className="theme-link hover:opacity-80" onClick={() => input.current?.click()} disabled={disabled}>browse files</button></p>
      <p className="mt-1 text-xs theme-muted">We extract reference text to keep the draft aligned with your documents.</p>
      <input id={inputId} ref={input} type="file" className="sr-only" multiple accept=".pdf,.docx,.txt,.md" disabled={disabled} onChange={(event) => { if (event.target.files) add(event.target.files); event.target.value = ""; }} />
    </div>
    {error && <p className="mt-2 text-xs text-rose-600">{error}</p>}
    {files.length > 0 && <ul className="mt-3 grid gap-2 sm:grid-cols-2">{files.map((file, index) => <li key={`${file.name}-${index}`} className="file-chip"><span className="file-type">{file.name.split(".").pop()?.toUpperCase() ?? "FILE"}</span><span className="min-w-0 flex-1 truncate">{file.name}</span><button type="button" onClick={() => onChange(files.filter((_, i) => i !== index))} disabled={disabled} aria-label={`Remove ${file.name}`} className="file-remove">×</button></li>)}</ul>}
    {onAnalyze && files.length > 0 && (
      <div className="mt-3">
        <button
          type="button"
          className="inline-flex items-center gap-2 rounded-md bg-[color:var(--accent)] px-3 py-1 text-sm font-semibold text-white"
          onClick={async () => {
            try {
              const res = await onAnalyze(files);
              if (onAnalyzeResult) onAnalyzeResult(res);
            } catch (err) {
              console.error(err);
            }
          }}
          disabled={disabled}
        >
          Analyze
        </button>
        <p className="mt-2 text-xs theme-muted">Analyze documents for relevant clauses (uses your prompt).</p>
      </div>
    )}
  </div>;
}
