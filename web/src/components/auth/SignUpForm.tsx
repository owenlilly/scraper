"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { describeAuthError } from "../../lib/auth-errors";
import { parseInviteHash } from "../../lib/invite-link";
import { MIN_PASSWORD, passwordProblem } from "../../lib/password";

type Invitation = { invite: string; email: string };
type Stage = { name: "reading" } | { name: "missing" } | { name: "ready"; invitation: Invitation } | { name: "sent"; email: string };

// The development server runs effects twice. The first run removes the token from the address bar, so the second would find nothing:
// keep what was read.
let captured: Invitation | null = null;

function readInvitation(): Invitation | null {
  const hash = window.location.hash;
  if (hash) {
    captured = parseInviteHash(hash);
    // Keep the token in memory only: take it out of the address bar and the browser's history.
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return captured;
}

export function SignUpForm() {
  const [stage, setStage] = useState<Stage>({ name: "reading" });
  const [name, setName] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const invitation = readInvitation();
    setStage(invitation ? { name: "ready", invitation } : { name: "missing" });
  }, []);

  async function submit(event: FormEvent, invitation: Invitation) {
    event.preventDefault();
    const problem = passwordProblem(password);
    if (problem) return setError(problem);
    setPending(true);
    setError(null);
    const { error: failure } = await authClient.signUp.email({
      email: invitation.email,
      password,
      name: name.trim(),
      callbackURL: "/verify-email",
      fetchOptions: { headers: { "x-invite-token": invitation.invite } },
    });
    if (failure) {
      setError(describeAuthError(failure, "sign-up"));
      setPending(false);
      return;
    }
    setStage({ name: "sent", email: invitation.email });
  }

  if (stage.name === "reading") return <p className="lede" aria-live="polite">Reading your invitation</p>;

  if (stage.name === "missing") {
    return (
      <>
        <h1>You need an invitation</h1>
        <p className="lede">Accounts are by invitation. Open the link in the invitation email, or ask the person who runs this site to send you a new one.</p>
        <div>
          <Link className="btn" href="/sign-in">
            Go to sign in
          </Link>
        </div>
      </>
    );
  }

  if (stage.name === "sent") {
    return (
      <>
        <h1>Check your inbox</h1>
        <p className="lede">We sent a confirmation link to {stage.email}. Open it to finish creating your account.</p>
      </>
    );
  }

  const { invitation } = stage;
  return (
    <>
      <div className="stack">
        <h1>Create your account</h1>
        <p className="lede">You were invited to Page Chat. Choose a password to finish.</p>
      </div>
      <form className="form" onSubmit={(event) => submit(event, invitation)}>
        <label htmlFor="email">
          Invited address
          <input id="email" name="email" type="email" value={invitation.email} readOnly />
          <span className="hint">The invitation works only for this address.</span>
        </label>
        <label htmlFor="name">
          Your name
          <input id="name" name="name" type="text" autoComplete="name" required value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label htmlFor="password">
          Password
          <input id="password" name="password" type="password" autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
          <span className="hint">At least {MIN_PASSWORD} characters. A few ordinary words work well.</span>
        </label>
        {error ? (
          <div className="error-box" role="alert">
            {error}
          </div>
        ) : null}
        <div className="actions">
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "Creating account" : "Create account"}
          </button>
        </div>
      </form>
    </>
  );
}
