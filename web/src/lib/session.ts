import { cache } from "react";
import { headers } from "next/headers";
import { redirect } from "next/navigation";
import { isActiveAdmin } from "./roles";
import { getServices } from "./services";

/** The signed-in person for this request, or null. Cached for the request, so every part of a page may ask. */
export const getSession = cache(async () => getServices().auth.api.getSession({ headers: await headers() }));

export async function requireSession() {
  const session = await getSession();
  if (!session) redirect("/sign-in");
  return session;
}

export async function requireAdmin() {
  const session = await requireSession();
  if (!isActiveAdmin(session.user)) redirect("/");
  return session;
}
