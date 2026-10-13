"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import {
  addRecord, asStrings, isChanged, moveRow, numberFrom, parseProblem, removeRecord, rowsFromSettings, setCell, singular, toggleRow,
  visibleFromRows, type FieldRow,
} from "../../lib/spiders/settings";
import type { FormField, SettingsView } from "../../lib/spiders/types";
import { Toast, useToast } from "../Toast";
import { messageOf, useSaveSettings, useSettings } from "./queries";

type Values = Record<string, unknown>;

export function SettingsForm({ name }: { name: string }) {
  const query = useSettings(name);
  const [toast, setToast] = useToast(); // here, not in the editor: the editor restarts after every save and would lose it
  if (query.isPending) {
    return (
      <p role="status" className="muted">
        Loading settings…
      </p>
    );
  }
  if (query.isError) {
    return (
      <div className="error-box" role="alert">
        {messageOf(query.error)}{" "}
        <button className="link" type="button" onClick={() => query.refetch()}>
          Try again
        </button>
      </div>
    );
  }
  // Keyed by the saved values, so after a save the form starts again from what was saved.
  return (
    <>
      <Editor key={JSON.stringify(query.data.settings)} name={name} view={query.data} onSaved={() => setToast("Settings saved")} />
      <Toast message={toast} />
    </>
  );
}

function NumberInput({ field, value, onChange, id }: { field: FormField; value: unknown; onChange: (next: number | null) => void; id: string }) {
  return (
    <input
      id={id}
      type="number"
      inputMode={field.type === "integer" ? "numeric" : "decimal"}
      step={field.type === "integer" ? 1 : "any"}
      min={field.minimum ?? undefined}
      max={field.maximum ?? undefined}
      value={typeof value === "number" ? value : ""}
      placeholder={field.nullable ? "Default" : undefined}
      onChange={(event) => onChange(numberFrom(event.target.value))}
    />
  );
}

function RecordsEditor({ field, value, onChange, problem }: { field: FormField; value: Values[]; onChange: (next: Values[]) => void; problem: string | null }) {
  const items = field.items ?? [];
  const full = field.maxItems !== null && value.length >= field.maxItems;
  const noun = singular(field.label);
  return (
    <div className="sp-section">
      <h2>{field.label}</h2>
      {field.description ? <p className="hint">{field.description}</p> : null}
      {value.length === 0 ? (
        <p className="sp-empty">No {noun}s yet. Add one so this spider has somewhere to run.</p>
      ) : (
        <div className="sp-table-wrap">
          <table className="sp-records">
            <thead>
              <tr>
                {items.map((item) => (
                  <th key={item.key} scope="col">
                    {item.label}
                  </th>
                ))}
                <th scope="col">
                  <span className="sr">Remove</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {value.map((record, row) => (
                <tr key={row}>
                  {items.map((item) => (
                    <td key={item.key}>
                      <input
                        type="text"
                        aria-label={`${item.label} of ${noun} ${row + 1}`}
                        placeholder={item.label}
                        maxLength={item.maxLength ?? undefined}
                        value={String(record[item.key] ?? "")}
                        onChange={(event) => onChange(setCell(value, row, item.key, event.target.value))}
                      />
                    </td>
                  ))}
                  <td className="act">
                    <button className="btn btn-small btn-quiet" type="button" aria-label={`Remove ${noun} ${row + 1}`} onClick={() => onChange(removeRecord(value, row))}>
                      Remove
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {problem ? (
        <p className="sp-bad" role="alert">
          {problem}
        </p>
      ) : null}
      <div>
        <button className="btn btn-small" type="button" disabled={full} onClick={() => onChange(addRecord(value, items))}>
          Add {noun}
        </button>
      </div>
    </div>
  );
}

function Editor({ name, view, onSaved }: { name: string; view: SettingsView; onSaved: () => void }) {
  const [values, setValues] = useState<Values>(() => ({ ...view.settings }));
  const [rows, setRows] = useState<FieldRow[]>(() => rowsFromSettings(view.fields, asStrings(view.settings.visible_fields)));
  const [problem, setProblem] = useState<string | null>(null);
  const save = useSaveSettings(name, onSaved, setProblem);

  const next: Values = { ...values, visible_fields: visibleFromRows(rows, view.fields) };
  const dirty = isChanged(next, view.settings);
  const where = parseProblem(problem);
  const about = (key: string) => (where?.key === key ? problem : null);
  const set = (key: string, value: unknown) => setValues((current) => ({ ...current, [key]: value }));

  function submit(event: FormEvent) {
    event.preventDefault();
    setProblem(null);
    save.mutate(next);
  }

  function discard() {
    setValues({ ...view.settings });
    setRows(rowsFromSettings(view.fields, asStrings(view.settings.visible_fields)));
    setProblem(null);
  }

  const common: FormField[] = [
    { key: "trial_max_requests", label: "Trial run requests", description: "How many requests a trial run starts with.", type: "integer", nullable: false, default: 5, minimum: 1, maximum: 20, minLength: null, maxLength: null, maxItems: null },
    { key: "max_requests", label: "Max requests per run", description: "Approximate when several requests run at once. Blank means no limit.", type: "integer", nullable: true, default: null, minimum: 1, maximum: 100000, minLength: null, maxLength: null, maxItems: null },
    { key: "request_delay", label: "Delay between requests (seconds)", description: "Blank keeps the spider's own.", type: "number", nullable: true, default: null, minimum: 0, maximum: 60, minLength: null, maxLength: null, maxItems: null },
    { key: "concurrency", label: "Requests at once", description: "Blank keeps the spider's own.", type: "integer", nullable: true, default: null, minimum: 1, maximum: 8, minLength: null, maxLength: null, maxItems: null },
  ];

  return (
    <form className="sp-form" onSubmit={submit} noValidate>
      {view.problems.length > 0 ? (
        <div className="error-box" role="alert">
          The saved settings are not valid, so runs are refused until they are fixed: {view.problems.join(" ")}
        </div>
      ) : null}
      {problem && !where ? (
        <div className="error-box" role="alert">
          {problem}
        </div>
      ) : null}

      <section className="sp-section" aria-labelledby="sp-fields-title">
        <h2 id="sp-fields-title">Fields shown in results</h2>
        <p className="hint">Tick the fields to show and put them in order. If nothing is ticked, every field is shown.</p>
        <ul className="sp-rows">
          {rows.map((row, index) => (
            <li key={row.name} className={row.shown ? "" : "off"}>
              <label className="name">
                <input type="checkbox" checked={row.shown} onChange={() => setRows((current) => toggleRow(current, index))} /> {row.name}
              </label>
              <span className="moves">
                <button className="btn btn-small btn-quiet" type="button" disabled={index === 0} aria-label={`Move ${row.name} up`} onClick={() => setRows((current) => moveRow(current, index, -1))}>
                  Up
                </button>
                <button className="btn btn-small btn-quiet" type="button" disabled={index === rows.length - 1} aria-label={`Move ${row.name} down`} onClick={() => setRows((current) => moveRow(current, index, 1))}>
                  Down
                </button>
              </span>
            </li>
          ))}
        </ul>
        {about("visible_fields") ? (
          <p className="sp-bad" role="alert">
            {about("visible_fields")}
          </p>
        ) : null}
      </section>

      <section className="sp-section" aria-labelledby="sp-limits-title">
        <h2 id="sp-limits-title">Trial runs and crawl limits</h2>
        <div className="sp-grid2">
          {common.map((field) => (
            <label key={field.key} className="sp-field" htmlFor={`f-${field.key}`}>
              {field.label}
              <NumberInput id={`f-${field.key}`} field={field} value={values[field.key]} onChange={(number) => set(field.key, number)} />
              {field.description ? <span className="hint">{field.description}</span> : null}
              {about(field.key) ? <span className="bad">{about(field.key)}</span> : null}
            </label>
          ))}
        </div>
      </section>

      {view.form.length > 0 ? (
        <section className="sp-section" aria-label={`${name} settings`}>
          {view.form.map((field) =>
            field.type === "array" ? (
              <RecordsEditor key={field.key} field={field} value={(values[field.key] as Values[] | undefined) ?? []} onChange={(list) => set(field.key, list)} problem={about(field.key)} />
            ) : (
              <label key={field.key} className="sp-field" htmlFor={`f-${field.key}`}>
                {field.label}
                {field.type === "integer" || field.type === "number" ? (
                  <NumberInput id={`f-${field.key}`} field={field} value={values[field.key]} onChange={(number) => set(field.key, number)} />
                ) : (
                  <input id={`f-${field.key}`} type="text" maxLength={field.maxLength ?? undefined} value={String(values[field.key] ?? "")} onChange={(event) => set(field.key, event.target.value)} />
                )}
                {field.description ? <span className="hint">{field.description}</span> : null}
                {about(field.key) ? <span className="bad">{about(field.key)}</span> : null}
              </label>
            ),
          )}
        </section>
      ) : null}

      <div className="sp-savebar">
        <button className="btn btn-primary" type="submit" disabled={!dirty || save.isPending}>
          Save settings
        </button>
        <button className="btn btn-quiet" type="button" disabled={!dirty || save.isPending} onClick={discard}>
          Discard changes
        </button>
        <Link className="sp-note-link" href={`/spiders/${name}`}>
          Back to {name}
        </Link>
      </div>
    </form>
  );
}
