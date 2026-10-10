import type { Pool } from "pg";
import { afterAll, beforeAll, beforeEach, describe, expect, it } from "vitest";
import { InviteStore } from "../src/lib/invites";
import { inviteOnlyPool } from "./helpers";

let pool: Pool;
let store: InviteStore;

beforeAll(async () => {
  pool = await inviteOnlyPool();
  store = new InviteStore(pool, 7);
});
afterAll(() => pool.end());
beforeEach(() => pool.query("TRUNCATE auth.invite"));

describe("InviteStore", () => {
  it("creates an invitation that checks out for exactly its address", async () => {
    const { invite, token } = await store.create("Alice@Example.com ", "admin-1");
    expect(invite.email).toBe("alice@example.com");
    expect(invite.status).toBe("pending");
    expect(invite.invitedBy).toBe("admin-1");
    expect(await store.check(token, "alice@example.com")).toEqual({ ok: true, id: invite.id });
    expect(await store.check(token, " ALICE@example.com")).toEqual({ ok: true, id: invite.id });
  });

  it("never stores the token, only a hash of it", async () => {
    const { token } = await store.create("a@x.test", null);
    const { rows } = await pool.query("SELECT token_hash FROM auth.invite");
    expect(rows[0].token_hash).toMatch(/^[0-9a-f]{64}$/);
    expect(rows[0].token_hash).not.toContain(token);
    expect(JSON.stringify(rows)).not.toContain(token);
  });

  it("makes long, different tokens", async () => {
    const a = await store.create("a@x.test", null);
    const b = await store.create("b@x.test", null);
    expect(a.token).not.toBe(b.token);
    expect(a.token.length).toBeGreaterThanOrEqual(43);
  });

  it("expires after the configured number of days", async () => {
    const { invite } = await store.create("a@x.test", null);
    const days = (invite.expiresAt.getTime() - invite.createdAt.getTime()) / 86_400_000;
    expect(days).toBeCloseTo(7, 1);
  });

  it.each([["", "missing"], ["not-a-real-token", "unknown"]])("refuses %j as %s", async (token, reason) => {
    await store.create("a@x.test", null);
    expect(await store.check(token, "a@x.test")).toEqual({ ok: false, reason });
  });

  it("refuses the right token for a different address", async () => {
    const { token } = await store.create("a@x.test", null);
    expect(await store.check(token, "someone-else@x.test")).toEqual({ ok: false, reason: "wrong_email" });
  });

  it("refuses an invitation once the address has an account", async () => {
    const { token } = await store.create("a@x.test", null);
    await store.consume("A@x.test");
    expect(await store.check(token, "a@x.test")).toEqual({ ok: false, reason: "used" });
  });

  it("refuses an expired invitation", async () => {
    const { token } = await store.create("a@x.test", null);
    await pool.query("UPDATE auth.invite SET expires_at = now() - interval '1 second'");
    expect(await store.check(token, "a@x.test")).toEqual({ ok: false, reason: "expired" });
  });

  it("revokes the older invitation when the same address is invited again", async () => {
    const first = await store.create("a@x.test", null);
    const second = await store.create("a@x.test", null);
    expect(await store.check(first.token, "a@x.test")).toEqual({ ok: false, reason: "revoked" });
    expect(await store.check(second.token, "a@x.test")).toMatchObject({ ok: true });
  });

  it("revokes a pending invitation once, and not a used or unknown one", async () => {
    const { invite, token } = await store.create("a@x.test", null);
    expect(await store.revoke(invite.id)).toBe(true);
    expect(await store.revoke(invite.id)).toBe(false);
    expect(await store.check(token, "a@x.test")).toEqual({ ok: false, reason: "revoked" });
    const used = await store.create("b@x.test", null);
    await store.consume("b@x.test");
    expect(await store.revoke(used.invite.id)).toBe(false);
    expect(await store.revoke("00000000-0000-0000-0000-000000000000")).toBe(false);
    expect(await store.revoke("'; DROP TABLE auth.invite; --")).toBe(false);
  });

  it("lists newest first with a status for each", async () => {
    await store.create("old@x.test", null);
    await store.create("used@x.test", null);
    await store.consume("used@x.test");
    await store.create("new@x.test", null);
    const listed = await store.list();
    expect(listed.map((i) => [i.email, i.status])).toEqual([["new@x.test", "pending"], ["used@x.test", "used"], ["old@x.test", "pending"]]);
  });
});
