/** What to tell a person when Better Auth or our hooks refuse something. Never shows a raw code or a server message. */

export type AuthContext = "sign-in" | "sign-up" | "forgot" | "reset" | "delete";
type AuthFailure = { code?: string; status?: number; message?: string } | null | undefined;

const GENERIC = "Something went wrong. Try again in a moment.";

export function describeAuthError(error: AuthFailure, context: AuthContext): string {
  if (!error) return GENERIC;
  if (error.status === 429) return "Too many attempts. Wait a minute and try again.";
  switch (error.code) {
    case "INVALID_EMAIL_OR_PASSWORD":
      return "The email address or password is not right. Check both and try again.";
    case "EMAIL_NOT_VERIFIED":
      return "Confirm your email address first. We sent you a link when you signed up.";
    case "BANNED_USER":
      return "This account has been suspended. Ask the person who invited you for help.";
    case "INVITE_REQUIRED":
      return "This invitation is not valid. It may have expired or already been used. Ask for a new one.";
    case "USER_ALREADY_EXISTS":
    case "USER_ALREADY_EXISTS_USE_ANOTHER_EMAIL":
      return "That address already has an account. Sign in instead.";
    case "PASSWORD_TOO_SHORT":
      return "Use at least 12 characters. A few ordinary words work well.";
    case "PASSWORD_TOO_LONG":
      return "That password is too long. Use at most 128 characters.";
    case "INVALID_TOKEN":
    case "TOKEN_EXPIRED":
      return context === "reset" ? "This link has expired or was already used. Ask for a new one." : "This link is not valid any more.";
    case "PASSWORD_REQUIRED":
      return "Enter your password to delete your account.";
    case "INVALID_PASSWORD":
      return context === "delete" ? "That password is not right." : "The email address or password is not right. Check both and try again.";
    case "ACCOUNT_DATA_NOT_DELETED":
      return error.status === 409
        ? "A run is still stopping, so nothing was deleted. Try again in a few seconds."
        : "Your saved data could not be deleted, so nothing was deleted. Try again shortly.";
    default:
      return GENERIC;
  }
}
