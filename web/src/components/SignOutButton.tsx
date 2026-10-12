"use client";

import { useRouter } from "next/navigation";
import { authClient } from "../lib/auth-client";

export function SignOutButton({ className = "btn" }: { className?: string }) {
  const router = useRouter();
  return (
    <button
      className={className}
      type="button"
      onClick={async () => {
        await authClient.signOut();
        router.replace("/sign-in");
        router.refresh();
      }}
    >
      Sign out
    </button>
  );
}
