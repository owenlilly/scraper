export const MIN_PASSWORD = 12;
export const MAX_PASSWORD = 128;

/** What is wrong with a new password, in words for the person, or null when it is fine. Counts characters people see. */
export function passwordProblem(password: string): string | null {
  const length = [...password].length;
  if (length < MIN_PASSWORD) return `Use at least ${MIN_PASSWORD} characters. A few ordinary words work well.`;
  if (length > MAX_PASSWORD) return `Use at most ${MAX_PASSWORD} characters.`;
  return null;
}
