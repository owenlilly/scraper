"use client";

import type { FormEvent, KeyboardEvent, RefObject } from "react";
import type { ModelView, ProviderView } from "../../lib/api/types";

export type Mode = "collect" | "ask";

export type ComposerProps = {
  textRef: RefObject<HTMLTextAreaElement | null>;
  text: string;
  onText: (value: string) => void;
  mode: Mode;
  onMode: (mode: Mode) => void;
  url: string;
  onUrl: (value: string) => void;
  onUrlCommit: () => void;
  follow: boolean;
  onFollow: (value: boolean) => void;
  depth: number;
  onDepth: (value: number) => void;
  browser: boolean;
  browserAvailable: boolean;
  onBrowser: (on: boolean) => void;
  providers: ProviderView[];
  provider: ProviderView;
  onProvider: (key: string) => void;
  models: ModelView[];
  model: string | null;
  onModel: (id: string) => void;
  modelsError: string | null;
  /** Why the last message was refused, such as a daily limit. */
  problem: string | null;
  working: boolean;
  reading: boolean;
  /** The agent has a run going, so what is typed goes to it as a note. */
  steering: boolean;
  onSend: () => void;
  onReadPage: () => void;
  onCancelRead: () => void;
};

export function Composer(p: ComposerProps) {
  const canSend = p.text.trim().length > 0 && p.model !== null && (p.mode === "collect" || !p.working);
  const placeholder =
    p.mode === "ask"
      ? p.working
        ? "A run is working. Switch to Collect to send it a note."
        : "Ask a question about the pages it has read"
      : p.steering
        ? "Send a note to the agent while it works"
        : "Tell it what to collect";

  // Why Send is greyed out, said where it can be seen (the placeholder disappears as soon as there is text in the box).
  const held =
    p.mode === "ask" && p.working
      ? "A run is working, so questions have to wait. Stop it with the Stop button above, or switch to Collect to send it a note."
      : p.model === null && !p.modelsError
        ? "No model is available, so nothing can be sent. Check the model server."
        : null;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (canSend) p.onSend();
  }
  function keyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
      event.preventDefault();
      if (canSend) p.onSend();
    }
  }

  return (
    <div className="composer-wrap">
      <form className="composer" onSubmit={submit}>
        <div className="row">
          <div className="seg" role="group" aria-label="What to do">
            <button type="button" aria-pressed={p.mode === "collect"} onClick={() => p.onMode("collect")}>
              Collect
            </button>
            <button type="button" aria-pressed={p.mode === "ask"} onClick={() => p.onMode("ask")}>
              Ask about the pages
            </button>
          </div>
          <span className="grow" />
          {p.providers.length > 1 ? (
            <>
              <label className="sr" htmlFor="provider">
                Model server
              </label>
              <select id="provider" value={p.provider.key} disabled={p.working} onChange={(event) => p.onProvider(event.target.value)}>
                {p.providers.map((provider) => (
                  <option key={provider.key} value={provider.key}>
                    {provider.name}
                  </option>
                ))}
              </select>
            </>
          ) : null}
          <label className="sr" htmlFor="model">
            Model
          </label>
          <select id="model" value={p.model ?? ""} disabled={p.models.length === 0} onChange={(event) => p.onModel(event.target.value)}>
            {p.models.length === 0 ? <option value="">No model available</option> : null}
            {p.models.map((model) => (
              <option key={model.id} value={model.id}>
                {model.id}
              </option>
            ))}
          </select>
        </div>
        <label className="sr" htmlFor="composer-text">
          Message
        </label>
        <textarea id="composer-text" ref={p.textRef} rows={2} value={p.text} placeholder={placeholder} onChange={(event) => p.onText(event.target.value)} onKeyDown={keyDown} />
        <div className="row">
          <label className="field-inline" htmlFor="start-url">
            {p.mode === "ask" ? "Page" : "Start from"}
            <input id="start-url" type="text" inputMode="url" value={p.url} placeholder="Optional: a page address" onChange={(event) => p.onUrl(event.target.value)} onBlur={p.onUrlCommit} />
          </label>
          {p.mode === "collect" && p.browserAvailable ? (
            <label className="field-inline" htmlFor="use-browser" title="Let the agent use a real browser to fill in forms, click, scroll and watch a page's network requests">
              <input id="use-browser" type="checkbox" checked={p.browser} disabled={p.working} onChange={(event) => p.onBrowser(event.target.checked)} />
              Use the browser
            </label>
          ) : null}
          {p.mode === "ask" ? (
            <>
              <label className="field-inline" htmlFor="read-follow">
                <input id="read-follow" type="checkbox" checked={p.follow} onChange={(event) => p.onFollow(event.target.checked)} />
                Also read linked pages
              </label>
              {p.follow ? (
                <label className="field-inline" htmlFor="read-depth">
                  Depth
                  <select id="read-depth" value={p.depth} onChange={(event) => p.onDepth(Number(event.target.value))}>
                    {[1, 2, 3].map((n) => (
                      <option key={n} value={n}>
                        {n}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              <button className="btn btn-small" type="button" disabled={p.reading ? false : !p.url.trim() || p.working} onClick={p.reading ? p.onCancelRead : p.onReadPage}>
                {p.reading ? "Cancel" : "Read page"}
              </button>
            </>
          ) : null}
          <span className="grow" />
          <button className="btn btn-primary send" type="submit" disabled={!canSend}>
            {p.mode === "collect" && p.steering ? "Send note" : "Send"}
          </button>
        </div>
        {held ? (
          <p className="muted small" role="status">
            {held}
          </p>
        ) : null}
        {p.provider.hosted ? <p className="muted small">Page text is sent to {p.provider.name}.</p> : null}
        {p.problem || p.modelsError ? (
          <p className="notice" role="alert">
            {p.problem ?? p.modelsError}
          </p>
        ) : null}
      </form>
    </div>
  );
}
