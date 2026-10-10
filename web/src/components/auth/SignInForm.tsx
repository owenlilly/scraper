"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { describeAuthError } from "../../lib/auth-errors";

const NOTICES = {
  deleted: "Your account was deleted.",
  reset: "Your password was changed. Sign in with the new one.",
} as const;

export function SignInForm({ notice }: { notice: keyof typeof NOTICES | null }) {
  const router = useRouter();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [unconfirmed, setUnconfirmed] = useState(false);
  const [resent, setResent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setError(null);
    setUnconfirmed(false);
    setResent(false);
    const { error: failure } = await authClient.signIn.email({ email, password });
    if (failure) {
      setError(describeAuthError(failure, "sign-in"));
      setUnconfirmed(failure.code === "EMAIL_NOT_VERIFIED");
      setPending(false);
      return;
    }
    router.replace("/");
    router.refresh();
  }

  async function sendAgain() {
    await authClient.sendVerificationEmail({ email, callbackURL: "/verify-email" });
    setResent(true);
  }

  return (
    <>
      <div className="stack">
        <h1>Sign in</h1>
        <p className="lede">Tell Page Chat what to collect from a website, and it reads the pages and hands you a table.</p>
      </div>
      {notice ? <div className="note-box">{NOTICES[notice]}</div> : null}
      <form className="form" onSubmit={submit}>
        <label htmlFor="email">
          Email address
          <input id="email" name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
        </label>
        <label htmlFor="password">
          Password
          <input id="password" name="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
        </label>
        {error ? (
          <div className="error-box" role="alert">
            {error}
            {unconfirmed ? (
              <>
                {" "}
                <button className="link" type="button" onClick={sendAgain}>
                  Send the link again
                </button>
              </>
            ) : null}
          </div>
        ) : null}
        {resent ? <div className="note-box">We sent a new confirmation link to {email}.</div> : null}
        <div className="actions">
          <button className="btn btn-primary" type="submit" disabled={pending}>
            {pending ? "Signing in" : "Sign in"}
          </button>
          <Link href="/forgot-password">Forgot your password?</Link>
        </div>
      </form>
      <p className="lede">Accounts are by invitation. Ask the person who runs this site to invite you.</p>
    </>
  );
}
