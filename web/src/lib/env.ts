import { isIP } from "node:net";

/** The web app's settings, read from the environment once and checked up front so a mistake fails at start-up, by name. */

export type WebEnv = {
  databaseUrl: string;
  authSecret: string;
  baseUrl: string;
  apiUrl: string;
  apiAudience: string;
  serviceSecret: string;
  emailTransport: "resend" | "log" | "mailpit";
  resendApiKey: string;
  emailFrom: string;
  mailpitUrl: string;
  adminEmail: string | null;
  trustedOrigins: string[];
  trustedProxies: string[];
  inviteTtlDays: number;
  production: boolean;
};

type Source = Record<string, string | undefined>;

function isHttpUrl(value: string): boolean {
  try {
    const url = new URL(value);
    return url.protocol === "http:" || url.protocol === "https:";
  } catch {
    return false;
  }
}

const csv = (value: string) =>
  value
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);

const trimSlash = (value: string) => value.replace(/\/+$/, "");

/** An IPv4 or IPv6 address, or a range such as 10.0.0.0/8. */
function isProxyEntry(entry: string): boolean {
  const parts = entry.split("/");
  if (parts.length > 2) return false;
  const family = isIP(parts[0]);
  if (!family) return false;
  if (parts.length === 1) return true;
  return /^\d{1,3}$/.test(parts[1]) && Number(parts[1]) <= (family === 4 ? 32 : 128);
}

export function loadEnv(source: Source = process.env): WebEnv {
  const problems: string[] = [];
  const get = (name: string) => (source[name] ?? "").trim();
  const production = source.NODE_ENV === "production";

  const databaseUrl = get("DATABASE_URL");
  if (!databaseUrl) problems.push("DATABASE_URL is required");

  const authSecret = get("BETTER_AUTH_SECRET");
  if (authSecret.length < 32) problems.push("BETTER_AUTH_SECRET must be at least 32 characters (try: openssl rand -base64 32)");

  const baseUrl = trimSlash(get("BETTER_AUTH_URL"));
  if (!isHttpUrl(baseUrl)) problems.push("BETTER_AUTH_URL must be this app's public address, such as https://app.example.com");
  else if (production && !baseUrl.startsWith("https://")) problems.push("BETTER_AUTH_URL must use https in production");

  const apiUrl = trimSlash(get("API_URL"));
  if (!isHttpUrl(apiUrl)) problems.push("API_URL must be the Python API's address, such as https://api.example.com");

  const apiAudience = trimSlash(get("API_AUDIENCE") || apiUrl);

  const serviceSecret = get("SERVICE_SECRET");
  if (serviceSecret.length < 24) problems.push("SERVICE_SECRET must be at least 24 characters and equal the API's SCRAPER_SERVICE_SECRET");

  const resendApiKey = get("RESEND_API_KEY");
  const requested = get("EMAIL_TRANSPORT");
  const emailTransport = (requested || (resendApiKey ? "resend" : "log")) as WebEnv["emailTransport"];
  if (emailTransport !== "resend" && emailTransport !== "log" && emailTransport !== "mailpit") problems.push("EMAIL_TRANSPORT must be resend, mailpit or log");
  if (emailTransport === "log" && production) problems.push("EMAIL_TRANSPORT=log would print invitation links to the server log; use resend in production");
  if (emailTransport === "mailpit" && production) problems.push("EMAIL_TRANSPORT=mailpit is for development, where Mailpit catches every message; use resend in production");
  const mailpitUrl = trimSlash(get("MAILPIT_URL") || "http://127.0.0.1:8025");
  if (emailTransport === "mailpit" && !isHttpUrl(mailpitUrl)) problems.push("MAILPIT_URL must be Mailpit's web address, such as http://127.0.0.1:8025");
  const emailFrom = get("EMAIL_FROM") || (emailTransport === "mailpit" ? "Page Chat <no-reply@localhost>" : "");
  if (emailTransport === "resend" && !resendApiKey) problems.push("RESEND_API_KEY is required when EMAIL_TRANSPORT is resend");
  if (emailTransport === "resend" && !emailFrom) problems.push("EMAIL_FROM is required when EMAIL_TRANSPORT is resend, such as Page Chat <no-reply@example.com>");

  const adminRaw = get("ADMIN_EMAIL").toLowerCase();
  if (adminRaw && !/^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(adminRaw)) problems.push("ADMIN_EMAIL must be an email address");

  const trustedOrigins = csv(get("TRUSTED_ORIGINS"));
  for (const origin of trustedOrigins) if (!isHttpUrl(origin.replace("*", "x"))) problems.push("TRUSTED_ORIGINS holds an entry that is not an address");

  const trustedProxies = csv(get("TRUSTED_PROXIES"));
  for (const entry of trustedProxies) if (!isProxyEntry(entry)) problems.push("TRUSTED_PROXIES holds an entry that is not an IP address or a range such as 10.0.0.0/8");

  const ttl = Number(get("INVITE_TTL_DAYS") || "7");
  if (!Number.isInteger(ttl) || ttl < 1 || ttl > 30) problems.push("INVITE_TTL_DAYS must be a whole number from 1 to 30");

  if (problems.length) throw new Error(`Invalid environment:\n- ${problems.join("\n- ")}`); // names only: values may be secrets

  return {
    databaseUrl, authSecret, baseUrl, apiUrl, apiAudience, serviceSecret, emailTransport, resendApiKey, emailFrom, mailpitUrl,
    adminEmail: adminRaw || null, trustedOrigins, trustedProxies, inviteTtlDays: ttl, production,
  };
}
