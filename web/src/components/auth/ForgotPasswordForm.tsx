"use client";

import Link from "next/link";
import { useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { describeAuthError } from "../../lib/auth-errors";

export function ForgotPasswordForm() {
  const [email, setEmail] = useState("");
  const [pending, setPending] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    const { error: failure } = await authClient.requestPasswordReset({ email, redirectTo: "/reset-password" });
    setPending(false);
    // The same answer whether or not the address has an account, so this page cannot be used to find out who does.
    if (failure?.status === 429) return setError(describeAuthError(failure, "forgot"));
    setSent(true);
  }

  if (sent) {
    return (
      <>
        <h1>Check your inbox</h1>
        <p className="lede">If {email} has an account, we sent a link to choose a new password. It works once and expires in an hour.</p>
        <div>
          <Link className="btn" href="/sign-in">
            Back to sign in
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      <div className="stack">
        <h1>Reset your password</h1>
        <p className="lede">Enter your email address and we will send you a link to choose a new password.</p>
      </div>
      <form className="form" onSubmit={submit}>
        <label htmlFor="email">
          Email address
          <input id="email" name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        {error ? (
          <div className="error-box" role="alert">
            {error}
          </div>
        ) : null}
        <div className="actions">
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "Sending" : "Send the link"}
          </button>
          <Link href="/sign-in">Back to sign in</Link>
        </div>
      </form>
    </>
  );
}
