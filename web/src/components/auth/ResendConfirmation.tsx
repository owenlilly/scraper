"use client";

import { useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";

export function ResendConfirmation() {
  const [email, setEmail] = useState("");
  const [sent, setSent] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    await authClient.sendVerificationEmail({ email, callbackURL: "/verify-email" });
    setSent(true); // the same answer for any address
  }

  if (sent) return <div className="note-box">If {email} has an account waiting to be confirmed, we sent a new link.</div>;
  return (
    <form className="form" onSubmit={submit}>
      <label htmlFor="email">
        Email address
        <input id="email" name="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      <div className="actions">
        <button className="btn btn-primary" type="submit">
          Send a new link
        </button>
      </div>
    </form>
  );
}
