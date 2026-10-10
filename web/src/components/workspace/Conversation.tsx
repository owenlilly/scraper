"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { ApiError } from "../../lib/api/client";
import type { WorkspaceState } from "../../lib/api/types";
import { deriveRunPanel, deriveThread, isWorking } from "../../lib/workspace/derive";
import type { RunController } from "../../lib/workspace/run";
import { useApi } from "./ApiProvider";
import { Composer, type Mode } from "./Composer";
import { RunPanel } from "./RunPanel";
import { EmptyThread, Thread } from "./Thread";
import "./conversation.css";
import { useRefreshState } from "../../lib/workspace/state";

const EXAMPLES = [
  "Collect every plan and price from shop.example/plans.",
  "List the title and date of each post on blog.example, newest first.",
  "Find the support email on example.org and say which page it is on.",
];

/** How far from the bottom still counts as "following" the conversation. */
const FOLLOW_PX = 80;

export function Conversation({ state, run, pendingDrafts, onNotice }: { state: WorkspaceState; run: RunController; pendingDrafts: string[]; onNotice: (text: string) => void }) {
  const api = useApi();
  const refresh = useRefreshState();
  const { live } = run;
  const textRef = useRef<HTMLTextAreaElement>(null);
  const threadRef = useRef<HTMLDivElement>(null);
  const following = useRef(true);

  const [text, setText] = useState("");
  const [mode, setMode] = useState<Mode>(state.settings.agent === "0" ? "ask" : "collect");
  const [url, setUrl] = useState(state.settings.url);
  const [follow, setFollow] = useState(state.settings.follow === "1");
  const [depth, setDepth] = useState(Math.min(3, Math.max(1, Number(state.settings.depth) || 1)));
  const [reading, setReading] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  const model = state.models.some((m) => m.id === state.model) ? state.model : (state.models[0]?.id ?? null);
  const working = isWorking(state, live);
  const items = useMemo(() => deriveThread(state, live), [state, live]);
  const panel = deriveRunPanel(state, live);
  const pagesRead = live ? live.log.filter((entry) => entry.name === "fetch_page" && entry.ok).length : 0;
  const lastMessage = state.history.at(-1);
  const retryable = !live && !working && lastMessage?.role === "assistant" && lastMessage.interrupted;

  // Messages the server handed back (typed after Stop, or never read) return to the box, ahead of anything typed since.
  const drafts = pendingDrafts.join("\n");
  useEffect(() => {
    if (!drafts) return;
    setText((current) => (current.includes(drafts) ? current : current ? `${drafts}\n${current}` : drafts));
    textRef.current?.focus();
  }, [drafts]);

  // Follow the newest text, unless the person has scrolled up to read.
  const newest = `${items.length}:${live?.answer.length ?? 0}:${live?.reasoning.length ?? 0}:${live?.log.length ?? 0}:${panel.kind}`;
  useEffect(() => {
    const thread = threadRef.current;
    if (thread && following.current) thread.scrollTop = thread.scrollHeight;
  }, [newest]);

  const save = (name: string, value: string) => api.put(`/settings/${name}`, { value }).catch(() => undefined);

  function pickMode(next: Mode) {
    setMode(next);
    void save("agent", next === "collect" ? "1" : "0");
  }

  async function send() {
    const message = text.trim();
    if (!message || !model) return;
    setProblem(null);
    if (mode !== "ask" && (working || panel.kind === "question")) {
      if ((await run.steer(message)) === "queued") setText("");
      return;
    }
    setText("");
    const refused = mode === "ask" ? await run.ask(message, model) : await run.start(message, model, url.trim() || undefined);
    if (refused) {
      setText((current) => current || message);
      setProblem(refused);
    }
  }

  /** Runs a request that carries on from something already on screen, and says why when the server refuses it. */
  async function continueWith(go: () => Promise<string | null>) {
    setProblem(null);
    setProblem(await go());
  }

  async function readPage() {
    setReading(true);
    try {
      await api.post("/scrape", { url: url.trim(), mode: "fast", follow, depth });
      await refresh();
    } catch (error) {
      const hint = error instanceof ApiError && typeof error.extra.hint === "string" ? ` ${error.extra.hint}` : "";
      onNotice(error instanceof ApiError ? `${error.message}${hint}` : "The page could not be read. Try again.");
    } finally {
      setReading(false);
    }
  }

  async function chooseModel(id: string) {
    await api.put("/model", { id }).catch((error) => onNotice(error instanceof ApiError ? error.message : "Could not switch models."));
    await refresh();
  }

  return (
    <div className="pane chat">
      <div
        className="thread"
        ref={threadRef}
        aria-live="polite"
        onScroll={(event) => {
          const el = event.currentTarget;
          following.current = el.scrollHeight - el.scrollTop - el.clientHeight < FOLLOW_PX;
        }}
      >
        {items.length === 0 ? (
          <EmptyThread
            examples={EXAMPLES}
            onPick={(example) => {
              setText(example);
              textRef.current?.focus();
            }}
          />
        ) : (
          <Thread items={items} onRetry={retryable && model ? () => void continueWith(() => run.retry(state.history.at(-2)?.content ?? "", model)) : undefined} />
        )}
      </div>
      <RunPanel
        panel={panel}
        pagesRead={pagesRead}
        onStop={() => void run.stop()}
        onResume={() => void continueWith(() => run.proceed(state.run))}
        onAnswer={(value) => void continueWith(() => run.answer(value, state.run))}
        onDiscard={() => void run.discard()}
      />
      <Composer
        textRef={textRef}
        text={text}
        onText={setText}
        mode={mode}
        onMode={pickMode}
        url={url}
        onUrl={setUrl}
        onUrlCommit={() => void save("url", url.trim())}
        follow={follow}
        onFollow={(value) => {
          setFollow(value);
          void save("follow", value ? "1" : "0");
        }}
        depth={depth}
        onDepth={(value) => {
          setDepth(value);
          void save("depth", String(value));
        }}
        models={state.models}
        model={model}
        onModel={(id) => void chooseModel(id)}
        modelsError={state.models_error}
        problem={problem}
        working={working}
        reading={reading}
        steering={working || panel.kind === "question"}
        onSend={() => void send()}
        onReadPage={() => void readPage()}
        onCancelRead={() => void api.post("/scrape/cancel").catch(() => undefined)}
      />
    </div>
  );
}
