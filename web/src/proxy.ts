import { NextResponse, type NextRequest } from "next/server";

/**
 * A quick, optimistic check that costs no database call: someone with no session cookie at all has no business on the workspace
 * or the administrator page, so send them to sign in. It does not prove the session is real; each of those pages checks that for itself.
 * The cookie is `better-auth.session_token`, with a `__Secure-` prefix in production.
 */
export function hasSessionCookie(cookieHeader: string): boolean {
  return cookieHeader.split(";").some((part) => {
    const [name, ...value] = part.trim().split("=");
    return name.endsWith("better-auth.session_token") && value.join("=").length > 0;
  });
}

export function proxy(request: NextRequest) {
  if (hasSessionCookie(request.headers.get("cookie") ?? "")) return NextResponse.next();
  return NextResponse.redirect(new URL("/sign-in", request.url));
}

export const config = { matcher: ["/", "/admin/:path*", "/spiders/:path*"] };
