import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";
import { AuthShell } from "../../components/auth/AuthShell";
import { ResendConfirmation } from "../../components/auth/ResendConfirmation";
import { getSession } from "../../lib/session";

export const metadata: Metadata = { title: "Confirm your email address" };

// The confirmation link in the email lands here. A good link has already signed the person in, so they go straight to the workspace.
export default async function VerifyEmailPage({ searchParams }: { searchParams: Promise<{ error?: string }> }) {
  const [session, params] = await Promise.all([getSession(), searchParams]);
  if (session && !params.error) redirect("/");
  if (params.error) {
    return (
      <AuthShell>
        <h1>This link has expired</h1>
        <p className="lede">Confirmation links work once and expire after an hour. Enter your address and we will send a new one.</p>
        <ResendConfirmation />
      </AuthShell>
    );
  }
  return (
    <AuthShell>
      <h1>Your address is confirmed</h1>
      <p className="lede">Sign in to carry on.</p>
      <div>
        <Link className="btn btn-primary" href="/sign-in">
          Sign in
        </Link>
      </div>
    </AuthShell>
  );
}
