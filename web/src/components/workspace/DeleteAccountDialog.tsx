"use client";

import { useRouter } from "next/navigation";
import { useEffect, useId, useState, type FormEvent } from "react";
import { authClient } from "../../lib/auth-client";
import { describeAuthError } from "../../lib/auth-errors";
import { Dialog } from "../Dialog";

/** Erases the account and everything saved for it. Asks for the password again, so a borrowed screen cannot do it. */
export function DeleteAccountDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const router = useRouter();
  const [password, setPassword] = useState("");
  const [pending, setPending] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const formId = useId();
  const fieldId = useId();

  useEffect(() => {
    if (!open) {
      setPassword("");
      setProblem(null);
    }
  }, [open]);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setPending(true);
    setProblem(null);
    const { error } = await authClient.deleteUser({ password });
    if (error) {
      setProblem(describeAuthError(error, "delete"));
      setPending(false);
      return;
    }
    router.replace("/sign-in?deleted=1");
    router.refresh();
  }

  return (
    <Dialog
      open={open}
      title="Delete your account?"
      onClose={onClose}
      actions={
        <>
          <button className="btn" type="button" onClick={onClose}>
            Keep my account
          </button>
          <button className="btn btn-danger" type="submit" form={formId} disabled={pending || !password}>
            Delete my account
          </button>
        </>
      }
    >
      <p>Your account, results and everything the agent saved for you are erased and cannot be recovered. Enter your password to confirm.</p>
      <form className="form" id={formId} onSubmit={submit}>
        <label htmlFor={fieldId}>
          Password
          <input id={fieldId} type="password" autoComplete="current-password" value={password} onChange={(event) => setPassword(event.target.value)} />
        </label>
        {problem ? (
          <p className="error-box" role="alert">
            {problem}
          </p>
        ) : null}
      </form>
    </Dialog>
  );
}
