"use client";

import { useId, useState } from "react";
import type { LimitPending, Pending } from "../../lib/api/types";
import type { RunPanel as Panel } from "../../lib/workspace/derive";

type Answer = string | { action?: string; note?: string; allow?: boolean };
type Handlers = { onAnswer: (value: Answer) => void; onDiscard: () => void };

function AskCard({ pending, onAnswer }: { pending: Extract<Pending, { kind: "ask" }>; onAnswer: (value: Answer) => void }) {
  const [text, setText] = useState("");
  const inputId = useId();
  return (
    <>
      {pending.options.length ? (
        <div className="options">
          {pending.options.map((option) => (
            <button key={option} className="btn" type="button" onClick={() => onAnswer(option)}>
              {option}
            </button>
          ))}
        </div>
      ) : null}
      <form
        className="free"
        onSubmit={(event) => {
          event.preventDefault();
          if (text.trim()) onAnswer(text.trim());
        }}
      >
        <label className="sr" htmlFor={inputId}>
          Your own answer
        </label>
        <input id={inputId} type="text" value={text} onChange={(event) => setText(event.target.value)} placeholder={pending.options.length ? "Or answer in your own words" : "Your answer"} />
        <button className="btn" type="submit" disabled={!text.trim()}>
          Send answer
        </button>
      </form>
    </>
  );
}

function LimitCard({ pending, onAnswer }: { pending: LimitPending; onAnswer: (value: Answer) => void }) {
  const [note, setNote] = useState("");
  const inputId = useId();
  return (
    <>
      <div className="free">
        <label className="sr" htmlFor={inputId}>
          A note for the agent (optional)
        </label>
        <input id={inputId} type="text" value={note} onChange={(event) => setNote(event.target.value)} placeholder="Add a note for the agent (optional)" />
      </div>
      <div className="options">
        {pending.actions.map((action) => (
          <button key={action.action} className={action.action === "continue" ? "btn btn-primary" : "btn"} type="button" onClick={() => onAnswer({ action: action.action, note: note.trim() })}>
            {action.label}
          </button>
        ))}
      </div>
    </>
  );
}

function QuestionCard({ pending, onAnswer, onDiscard }: { pending: Pending } & Handlers) {
  const labelId = useId();
  return (
    <div className="question" role="group" aria-labelledby={labelId}>
      <p className="q" id={labelId}>
        {pending.question}
      </p>
      {pending.kind === "ask" ? <AskCard pending={pending} onAnswer={onAnswer} /> : null}
      {pending.kind === "limit" ? <LimitCard pending={pending} onAnswer={onAnswer} /> : null}
      {pending.kind === "site" ? (
        <div className="options">
          <button className="btn btn-primary" type="button" onClick={() => onAnswer({ allow: true })}>
            Allow {pending.host}
          </button>
          <button className="btn" type="button" onClick={() => onAnswer({ allow: false })}>
            Do not allow
          </button>
        </div>
      ) : null}
      <div>
        <button className="btn btn-quiet btn-small" type="button" onClick={onDiscard}>
          Discard this run
        </button>
      </div>
    </div>
  );
}

/** What the run needs from the person right now: nothing (it is working), an answer, or a decision to resume or discard. */
export function RunPanel({ panel, pagesRead, onStop, onResume, onAnswer, onDiscard }: { panel: Panel; pagesRead: number; onStop: () => void; onResume: () => void } & Handlers) {
  if (panel.kind === "none") return null;
  if (panel.kind === "question") return <QuestionCard pending={panel.pending} onAnswer={onAnswer} onDiscard={onDiscard} />;
  if (panel.kind === "working") {
    return (
      <div className="live" role="status">
        <div className="line">
          <span className="pulse" aria-hidden="true" />
          <span className="grow">{panel.doing}</span>
          <button className="btn btn-small" type="button" onClick={onStop}>
            Stop
          </button>
        </div>
        <span className="muted small">
          {panel.step > 0 ? `Step ${panel.step}` : "Starting"}
          {pagesRead > 0 ? `, ${pagesRead} ${pagesRead === 1 ? "page" : "pages"} read` : ""}
        </span>
      </div>
    );
  }
  return (
    <div className="live stopped" role="status">
      <div className="line">
        <span className="pulse" aria-hidden="true" />
        <span className="grow">{panel.message}</span>
        {panel.resumable ? (
          <button className="btn btn-small btn-primary" type="button" onClick={onResume}>
            Resume
          </button>
        ) : null}
        <button className="btn btn-small" type="button" onClick={onDiscard}>
          Discard this run
        </button>
      </div>
    </div>
  );
}
