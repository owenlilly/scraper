import { describeCall, hostAndPath } from "../../lib/api/format";
import { summarizeWork, type ThreadItem, type TraceLine } from "../../lib/workspace/derive";
import { Prose } from "./Prose";

export function WorkLog({ trace, open }: { trace: TraceLine[]; open?: boolean }) {
  if (trace.length === 0) return null;
  return (
    <details className="worklog" open={open}>
      <summary>{summarizeWork(trace)}</summary>
      <ul>
        {trace.map((line, index) => (
          <li key={index} className={line.ok ? undefined : "failed"}>
            {line.done ? describeCall(line.name, line.args) : `${describeCall(line.name, line.args)} …`}
            {line.ok ? null : <span className="why"> ({line.text || "it failed"})</span>}
          </li>
        ))}
      </ul>
    </details>
  );
}

export function EmptyThread({ examples, onPick }: { examples: string[]; onPick?: (text: string) => void }) {
  return (
    <div className="empty">
      <h2>What should it collect?</h2>
      <p>Describe the data you want and where to find it. It reads the pages, asks you when something is unclear, and puts what it finds in a table you can download.</p>
      {onPick ? (
        <div className="examples">
          {examples.map((example) => (
            <button key={example} type="button" onClick={() => onPick(example)}>
              {example}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

const isWebAddress = (url: string) => /^https?:\/\//i.test(url);

export function Thread({ items, onRetry }: { items: ThreadItem[]; onRetry?: () => void }) {
  const last = items.length - 1;
  return (
    <>
      {items.map((item, index) =>
        item.kind === "user" ? (
          <div key={item.key} className={item.queued ? "msg-user queued" : "msg-user"}>
            {item.text}
            {item.queued ? <span className="note">Waiting for the agent to read this</span> : null}
          </div>
        ) : (
          <div key={item.key} className="msg-agent">
            <WorkLog trace={item.trace} open={item.live} />
            {item.reasoning ? (
              <details className="reasoning" open={item.live && !item.answer}>
                <summary>Reasoning</summary>
                <div>{item.reasoning}</div>
              </details>
            ) : null}
            {item.answer ? <Prose text={item.answer} /> : item.live && !item.reasoning && item.trace.length === 0 ? <p className="thinking">Thinking…</p> : null}
            {item.sources.length ? (
              <ul className="sources" aria-label="Pages this answer used">
                {item.sources.filter(isWebAddress).map((source) => (
                  <li key={source}>
                    <a href={source} target="_blank" rel="noopener noreferrer">
                      {hostAndPath(source)}
                    </a>
                  </li>
                ))}
              </ul>
            ) : null}
            {item.note ? <p className="note-line">{item.note}</p> : null}
            {item.interrupted ? (
              <p className="note-line">
                This answer was cut off.{" "}
                {index === last && onRetry ? (
                  <button className="link" type="button" onClick={onRetry}>
                    Ask again
                  </button>
                ) : null}
              </p>
            ) : null}
            {item.notice ? (
              <p className="notice" role="alert">
                {item.notice}
              </p>
            ) : null}
          </div>
        ),
      )}
    </>
  );
}
