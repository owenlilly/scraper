"use client";

import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { describeAuthError } from "../../lib/auth-errors";
import { MIN_PASSWORD, passwordProblem } from "../../lib/password";

export function ResetPasswordForm() {
  const router = useRouter();
  const params = useSearchParams();
  const token = params.get("token");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!token || params.get("error")) {
    return (
      <>
        <h1>This link has expired</h1>
        <p className="lede">Reset links work once and expire after an hour. Ask for a new one.</p>
        <div>
          <Link className="btn" href="/forgot-password">
            Send a new link
          </Link>
        </div>
      </>
    );
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const problem = passwordProblem(password);
    if (problem) return setError(problem);
    setPending(true);
    setError(null);
    const { error: failure } = await authClient.resetPassword({ newPassword: password, token: token as string });
    if (failure) {
      setError(describeAuthError(failure, "reset"));
      setPending(false);
      return;
    }
    router.replace("/sign-in?reset=1");
  }

  return (
    <>
      <div className="stack">
        <h1>Choose a new password</h1>
        <p className="lede">You will be signed out everywhere else.</p>
      </div>
      <form className="form" onSubmit={submit}>
        <label htmlFor="password">
          New password
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
            {pending ? "Saving" : "Save new password"}
          </button>
        </div>
      </form>
    </>
  );
}
