"use client";

import { useState } from "react";
import type { Question } from "@/lib/types";

const INPUT_TYPE: Record<Question["type"], string> = {
  date: "date",
  money: "text",
  duration: "text",
  text: "text",
  enum: "text",
};

const PLACEHOLDER: Record<Question["type"], string> = {
  date: "",
  money: "e.g. 250000",
  duration: "e.g. 3 years",
  text: "",
  enum: "",
};

/**
 * The agent asked rather than guessed. Every field is required: an unanswered question means
 * a value the contract cannot have, and the backend will refuse to render a blank clause.
 */
export function Questions({
  questions,
  onSubmit,
  busy,
}: {
  questions: Question[];
  onSubmit: (answers: Record<string, string>) => void;
  busy: boolean;
}) {
  const [answers, setAnswers] = useState<Record<string, string>>({});
  const complete = questions.every((q) => (answers[q.name] ?? "").trim().length > 0);

  return (
    <form
      className="question-panel"
      onSubmit={(event) => {
        event.preventDefault();
        if (complete && !busy) onSubmit(answers);
      }}
    >
      <p className="eyebrow theme-link">One more step</p>
      <h2 className="mt-2 text-lg font-semibold tracking-tight">The agent needs a few details</h2>
      <p className="mt-1 text-sm theme-muted">
        It will not invent these. Nothing is drafted until you answer.
      </p>

      <div className="mt-6 space-y-4">
        {questions.map((question) => (
          <label key={question.name} className="block">
            <span className="field-label">{question.question}</span>
            <input
              className="field-input"
              type={INPUT_TYPE[question.type]}
              placeholder={PLACEHOLDER[question.type]}
              value={answers[question.name] ?? ""}
              required
              onChange={(event) =>
                setAnswers((current) => ({ ...current, [question.name]: event.target.value }))
              }
            />
            <span className="mt-2 block font-mono text-[10px] text-[color:var(--text-muted)]">
              {question.name}
            </span>
          </label>
        ))}
      </div>

      <button
        type="submit"
        disabled={!complete || busy}
        className="primary-action mt-6 disabled:opacity-40"
      >
        {busy ? "Working…" : "Continue drafting"}
      </button>
    </form>
  );
}
