import type { Metadata } from "next";
import { redirect } from "next/navigation";
import { AuthShell } from "../../components/auth/AuthShell";
import { SignInForm } from "../../components/auth/SignInForm";
import { getSession } from "../../lib/session";

export const metadata: Metadata = { title: "Sign in" };

export default async function SignInPage({ searchParams }: { searchParams: Promise<{ deleted?: string; reset?: string }> }) {
  const [session, params] = await Promise.all([getSession(), searchParams]); // together: neither waits for the other
  if (session) redirect("/");
  return (
    <AuthShell>
      <SignInForm notice={params.deleted ? "deleted" : params.reset ? "reset" : null} />
    </AuthShell>
  );
}
