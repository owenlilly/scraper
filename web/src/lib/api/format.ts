/** Words for what the agent is doing, and small text helpers. */

/** `https://www.shop.example/plans/?a=1#top` becomes `shop.example/plans/?a=1`. Anything that is not an address is left alone. */
export function hostAndPath(url: string): string {
  try {
    const parsed = new URL(url);
    const path = parsed.pathname === "/" ? "" : parsed.pathname.replace(/\/$/, "");
    return `${parsed.host.replace(/^www\./, "")}${path}${parsed.search}`;
  } catch {
    return url;
  }
}

/** Tools whose result is a page the agent has now read. */
const PAGE_TOOLS = new Set(["fetch_page", "browser_submit_form", "browser_click", "browser_scroll"]);
export const readsPage = (name: string): boolean => PAGE_TOOLS.has(name);

export const plural = (count: number, word: string) => `${count} ${word}${count === 1 ? "" : "s"}`;

export function describeCall(name: string, args: Record<string, unknown>): string {
  const url = typeof args.url === "string" && args.url ? hostAndPath(args.url) : null;
  switch (name) {
    case "fetch_page":
      return url ? `Fetching ${url}${typeof args.mode === "string" && args.mode ? ` in ${args.mode} mode` : ""}` : "Fetching a page";
    case "web_search":
      return typeof args.query === "string" && args.query ? `Searching the web for "${args.query.length > 80 ? `${args.query.slice(0, 80)}…` : args.query}"` : "Searching the web";
    case "browser_submit_form":
      return url ? `Submitting a form on ${url}` : "Submitting a form";
    case "browser_click":
      return typeof args.click === "string" && args.click ? `Clicking "${args.click}" on ${url ?? "a page"}` : "Clicking on a page";
    case "browser_scroll":
      return url ? `Scrolling ${url}` : "Scrolling a page";
    case "browser_network":
      return `Watching the network requests of ${url ?? "a page"}`;
    case "list_links":
      return `Listing the links on ${url ?? "a page"}`;
    case "read_page":
      return `Reading ${url ?? "a page"}`;
    case "extract":
      return `Pulling ${String(args.selector ?? "")} from ${url ?? "a page"}`.replace("Pulling  from", "Pulling data from");
    case "add_rows":
      return `Adding ${plural(Array.isArray(args.rows) ? args.rows.length : 0, "row")} to the results`;
    case "set_fetch_delay": {
      const seconds = Number(args.seconds);
      const reason = typeof args.reason === "string" && args.reason ? `: ${args.reason}` : "";
      return `Waiting ${plural(Number.isFinite(seconds) ? seconds : 0, "second")} between pages${reason}`;
    }
    case "ask_user":
      return "Asking you a question";
    default:
      return name;
  }
}

export function shorten(text: string, limit = 1500): string {
  return text.length <= limit ? text : `${text.slice(0, limit).trimEnd()}\n[shortened]`;
}
