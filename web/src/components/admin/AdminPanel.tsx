"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { formatDay, inviteStatusLabel, personStatus, roleLabel } from "../../lib/admin-view";
import type { InviteStatus } from "../../lib/invites";
import { Dialog } from "../Dialog";
import "./admin.css";

export type InviteRow = { id: string; email: string; status: InviteStatus; createdAt: string };
export type PersonRow = { id: string; name: string; email: string; role: string | null; banned: boolean };

const PILL: Record<InviteStatus, string> = { pending: "waiting", used: "used", revoked: "revoked", expired: "expired" };

async function invite(email: string): Promise<string | null> {
  const response = await fetch("/api/admin/invites", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ email }) });
  if (response.ok) return null;
  const body = (await response.json().catch(() => null)) as { error?: { message?: string } } | null;
  return body?.error?.message ?? "The invitation could not be sent. Try again.";
}

export function AdminPanel({ invites, people, selfId }: { invites: InviteRow[]; people: PersonRow[]; selfId: string }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [toast, setToast] = useState<string | null>(null);
  const [removing, setRemoving] = useState<PersonRow | null>(null);

  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(null), 3200);
    return () => clearTimeout(timer);
  }, [toast]);

  async function act(work: () => Promise<string | null>, done: string) {
    setBusy(true);
    const failure = await work().catch(() => "Something went wrong. Try again.");
    setBusy(false);
    if (failure) setToast(failure);
    else {
      setToast(done);
      router.refresh();
    }
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setProblem(null);
    const failure = await invite(email.trim()).catch(() => "Something went wrong. Try again.");
    setBusy(false);
    if (failure) return setProblem(failure);
    setToast(`Invitation sent to ${email.trim()}`);
    setEmail("");
    router.refresh();
  }

  const apiError = (result: { error: { message?: string } | null }) => (result.error ? (result.error.message ?? "That did not work. Try again.") : null);

  return (
    <div className="admin">
      <div className="admin-inner">
        <h1>Invitations and people</h1>

        <section aria-labelledby="invite-heading">
          <h2 id="invite-heading">Invite someone</h2>
          <form className="invite-form" onSubmit={send}>
            <label htmlFor="invite-email">
              Email address
              <input id="invite-email" type="email" placeholder="name@example.com" required value={email} onChange={(event) => setEmail(event.target.value)} />
            </label>
            <button className="btn btn-primary" type="submit" disabled={busy}>
              Send invitation
            </button>
          </form>
          {problem ? (
            <p className="error-box" role="alert">
              {problem}
            </p>
          ) : null}
          <p className="muted small">The invitation works once, only for this address, and expires after 7 days.</p>
        </section>

        <section aria-labelledby="invitations-heading">
          <h2 id="invitations-heading">Invitations</h2>
          <div className="table-wrap">
            <table className="plain">
              <thead>
                <tr>
                  <th scope="col">Address</th>
                  <th scope="col">Status</th>
                  <th scope="col">Sent</th>
                  <th scope="col">
                    <span className="sr">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {invites.length === 0 ? (
                  <tr>
                    <td colSpan={4} className="muted">
                      No invitations yet.
                    </td>
                  </tr>
                ) : null}
                {invites.map((row) => (
                  <tr key={row.id}>
                    <td>{row.email}</td>
                    <td>
                      <span className={`pill ${PILL[row.status]}`}>{inviteStatusLabel(row.status)}</span>
                    </td>
                    <td>{formatDay(row.createdAt)}</td>
                    <td className="act">
                      {row.status === "pending" ? (
                        <button
                          className="btn btn-small"
                          type="button"
                          disabled={busy}
                          onClick={() =>
                            act(async () => {
                              const response = await fetch(`/api/admin/invites/${row.id}`, { method: "DELETE" });
                              return response.ok ? null : "The invitation could not be revoked. Try again.";
                            }, "Invitation revoked")
                          }
                        >
                          Revoke
                        </button>
                      ) : null}
                      {row.status === "expired" || row.status === "revoked" ? (
                        <button className="btn btn-small" type="button" disabled={busy} onClick={() => act(() => invite(row.email), `Invitation sent to ${row.email}`)}>
                          Invite again
                        </button>
                      ) : null}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>

        <section aria-labelledby="people-heading">
          <h2 id="people-heading">People</h2>
          <div className="table-wrap">
            <table className="plain">
              <thead>
                <tr>
                  <th scope="col">Name</th>
                  <th scope="col">Address</th>
                  <th scope="col">Role</th>
                  <th scope="col">Status</th>
                  <th scope="col">
                    <span className="sr">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {people.map((person) => (
                  <tr key={person.id}>
                    <td>{person.name}</td>
                    <td>{person.email}</td>
                    <td>{roleLabel(person.role)}</td>
                    <td>
                      <span className={`pill ${person.banned ? "banned" : "active"}`}>{personStatus(person)}</span>
                    </td>
                    <td className="act">
                      {person.id === selfId ? (
                        <span className="muted small">You</span>
                      ) : (
                        <>
                          <button
                            className="btn btn-small"
                            type="button"
                            disabled={busy}
                            onClick={() =>
                              act(
                                async () => apiError(person.banned ? await authClient.admin.unbanUser({ userId: person.id }) : await authClient.admin.banUser({ userId: person.id })),
                                person.banned ? `${person.name} can sign in again` : `${person.name} is banned`,
                              )
                            }
                          >
                            {person.banned ? "Lift ban" : "Ban"}
                          </button>{" "}
                          <button className="btn btn-small btn-danger" type="button" disabled={busy} onClick={() => setRemoving(person)}>
                            Remove
                          </button>
                        </>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      </div>

      <Dialog
        open={removing !== null}
        title={`Remove ${removing?.name ?? "this person"}?`}
        onClose={() => setRemoving(null)}
        actions={
          <>
            <button className="btn" type="button" onClick={() => setRemoving(null)}>
              Keep this account
            </button>
            <button
              className="btn btn-danger"
              type="button"
              disabled={busy}
              onClick={() => {
                const person = removing;
                setRemoving(null);
                if (person) void act(async () => apiError(await authClient.admin.removeUser({ userId: person.id })), "Account removed, and its saved data erased");
              }}
            >
              Remove account
            </button>
          </>
        }
      >
        <p>
          {removing?.email}'s account, results and everything the agent saved for them are erased and cannot be recovered.
        </p>
      </Dialog>

      {toast ? (
        <div className="toast" role="status">
          {toast}
        </div>
      ) : null}
    </div>
  );
}
