"use client";

import Link from "next/link";
import { useState } from "react";
import { isActive } from "../../lib/spiders/derive";
import { capChoices, locationOptions, runBlock } from "../../lib/spiders/settings";
import { Toast, useToast } from "../Toast";
import { messageOf, useFetchFeed, useRun, useRunActions, useRuns, useSettings } from "./queries";
import { RunHeader } from "./RunHeader";
import { RunLedger } from "./RunLedger";
import { RunList } from "./RunList";
import { TrialResults } from "./TrialResults";
import { useNow } from "./useNow";

export function SpiderPage({ name }: { name: string }) {
  const runs = useRuns(name);
  const settings = useSettings(name);
  const view = settings.data;
  const places = locationOptions(view);
  const hasLocations = Boolean(view?.locations_setting);
  const [picked, setPicked] = useState<number | null>(null); // null follows the newest run
  const [cap, setCap] = useState<number | null>(null); // null follows the saved trial default
  const [place, setPlace] = useState<string | null>(null); // null is the first location
  const [toast, setToast] = useToast();
  const selectedId = picked ?? runs.data?.[0]?.id ?? null;
  const run = useRun(selectedId);
  const current = run.data; // a local, so TypeScript keeps the narrowing inside the handlers below
  const running = current ? isActive(current.status) : false;
  const feed = useFetchFeed(selectedId, running);
  const { start, stop, retry } = useRunActions(setToast, (created) => setPicked(created.id));
  const inFlight = runs.data?.some((item) => isActive(item.status)) ?? false;
  const now = useNow(inFlight);
  const busy = start.isPending || stop.isPending || retry.isPending;

  const savedTrial = typeof view?.settings.trial_max_requests === "number" ? view.settings.trial_max_requests : 5;
  const trialCap = cap ?? savedTrial;
  const locationId = places.some((option) => option.id === place) ? place! : (places[0]?.id ?? undefined);
  const needsLocation = hasLocations && places.length === 0;
  const invalid = (view?.problems.length ?? 0) > 0;
  const block = runBlock({ busy, inFlight, loading: settings.isPending, failed: settings.isError, needsLocation, invalid });
  const blocked = block !== null;

  return (
    <>
      <div className="sp-head">
        <div>
          <Link className="sp-back small" href="/spiders">
            Spiders
          </Link>
          <h1>{name}</h1>
        </div>
        <div className="sp-controls">
          {places.length > 0 ? (
            <label>
              Location
              <select aria-label="Location" value={locationId} onChange={(event) => setPlace(event.target.value)}>
                {places.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <label>
            Trial pages
            <select aria-label="Trial pages" value={trialCap} onChange={(event) => setCap(Number(event.target.value))}>
              {capChoices(savedTrial).map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
          </label>
          <button className="btn" type="button" disabled={blocked} onClick={() => start.mutate({ name, kind: "trial", maxRequests: trialCap, location: locationId })}>
            Trial run
          </button>
          <button className="btn btn-primary" type="button" disabled={blocked} onClick={() => start.mutate({ name, kind: "live", location: locationId })}>
            Run now
          </button>
          <Link className="btn btn-quiet" href={`/spiders/${name}/settings`}>
            Settings
          </Link>
        </div>
      </div>
      {settings.isError ? (
        <div className="error-box" role="alert">
          Could not load this spider's settings, so runs cannot start. {messageOf(settings.error)}{" "}
          <button className="link" type="button" onClick={() => settings.refetch()}>
            Try again
          </button>
        </div>
      ) : null}
      {needsLocation ? <p className="muted small">Add a location in Settings to run this spider.</p> : null}
      {invalid ? <p className="sp-notice">The saved settings are not valid, so runs are refused. Fix them in Settings.</p> : null}
      {inFlight ? <p className="muted small">A run is already in flight, so a new one cannot start until it ends.</p> : null}

      {runs.isPending ? (
        <p role="status" className="muted">
          Loading runs…
        </p>
      ) : runs.isError ? (
        <div className="error-box" role="alert">
          {messageOf(runs.error)}{" "}
          <button className="link" type="button" onClick={() => runs.refetch()}>
            Try again
          </button>
        </div>
      ) : runs.data.length === 0 ? (
        <p className="sp-empty">No runs yet. Run a trial first to check this spider against the site.</p>
      ) : (
        <div className="sp-grid">
          <RunList runs={runs.data} selectedId={selectedId} onSelect={setPicked} now={now} />
          <div className="sp-main">
            {run.isError ? (
              <div className="error-box" role="alert">
                {messageOf(run.error)}{" "}
                <button className="link" type="button" onClick={() => run.refetch()}>
                  Try again
                </button>
              </div>
            ) : !current ? (
              <p role="status" className="muted">
                Loading run…
              </p>
            ) : (
              <>
                <RunHeader run={current} now={now} busy={busy} onStop={() => stop.mutate(current.id)} onRetry={() => retry.mutate(current.id)} />
                <RunLedger rows={feed.data} running={running} />
                <TrialResults key={current.id} run={current} view={view} />
              </>
            )}
          </div>
        </div>
      )}
      <Toast message={toast} />
    </>
  );
}
