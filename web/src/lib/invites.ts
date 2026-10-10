import { createHash, randomBytes } from "node:crypto";
import type { Pool } from "pg";

export const INVITE_TABLE_SQL = `
CREATE TABLE IF NOT EXISTS auth.invite (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  email text NOT NULL,
  token_hash text NOT NULL UNIQUE,
  invited_by text,
  created_at timestamptz NOT NULL DEFAULT now(),
  expires_at timestamptz NOT NULL,
  used_at timestamptz,
  revoked_at timestamptz
);
CREATE INDEX IF NOT EXISTS invite_email ON auth.invite (email);
`;

export type InviteStatus = "pending" | "used" | "revoked" | "expired";
export type Invite = {
  id: string;
  email: string;
  invitedBy: string | null;
  createdAt: Date;
  expiresAt: Date;
  usedAt: Date | null;
  revokedAt: Date | null;
  status: InviteStatus;
};
export type InviteCheck = { ok: true; id: string } | { ok: false; reason: "missing" | "unknown" | "wrong_email" | "used" | "revoked" | "expired" };

export const normalizeEmail = (email: string) => email.trim().toLowerCase();
const hashToken = (token: string) => createHash("sha256").update(token).digest("hex");

const COLUMNS = `id, email, invited_by, created_at, expires_at, used_at, revoked_at,
  CASE WHEN used_at IS NOT NULL THEN 'used' WHEN revoked_at IS NOT NULL THEN 'revoked' WHEN expires_at <= now() THEN 'expired' ELSE 'pending' END AS status`;

type Row = {
  id: string;
  email: string;
  invited_by: string | null;
  created_at: Date;
  expires_at: Date;
  used_at: Date | null;
  revoked_at: Date | null;
  status: InviteStatus;
};

const toInvite = (row: Row): Invite => ({
  id: row.id,
  email: row.email,
  invitedBy: row.invited_by,
  createdAt: row.created_at,
  expiresAt: row.expires_at,
  usedAt: row.used_at,
  revokedAt: row.revoked_at,
  status: row.status,
});

/** Invitations. The token that goes in the email is never stored, only its hash, so a copy of the table cannot be used to sign up. */
export class InviteStore {
  constructor(
    private readonly pool: Pool,
    private readonly ttlDays: number,
  ) {}

  /** A new invitation replaces any earlier one still waiting for the same address. */
  async create(email: string, invitedBy: string | null): Promise<{ invite: Invite; token: string }> {
    const address = normalizeEmail(email);
    const token = randomBytes(32).toString("base64url");
    const client = await this.pool.connect();
    try {
      await client.query("BEGIN");
      await client.query("UPDATE auth.invite SET revoked_at = now() WHERE email = $1 AND used_at IS NULL AND revoked_at IS NULL", [address]);
      const { rows } = await client.query<Row>(
        `INSERT INTO auth.invite (email, token_hash, invited_by, expires_at) VALUES ($1, $2, $3, now() + make_interval(days => $4))
         RETURNING ${COLUMNS}`,
        [address, hashToken(token), invitedBy, this.ttlDays],
      );
      await client.query("COMMIT");
      return { invite: toInvite(rows[0]), token };
    } catch (error) {
      await client.query("ROLLBACK");
      throw error;
    } finally {
      client.release();
    }
  }

  /** Is `token` a live invitation for exactly this address? The reason is for tests and logs, never for the person. */
  async check(token: string, email: string): Promise<InviteCheck> {
    if (!token) return { ok: false, reason: "missing" };
    const { rows } = await this.pool.query<Row>(`SELECT ${COLUMNS} FROM auth.invite WHERE token_hash = $1`, [hashToken(token)]);
    const row = rows[0];
    if (!row) return { ok: false, reason: "unknown" };
    if (row.email !== normalizeEmail(email)) return { ok: false, reason: "wrong_email" };
    if (row.status === "used") return { ok: false, reason: "used" };
    if (row.status === "revoked") return { ok: false, reason: "revoked" };
    if (row.status === "expired") return { ok: false, reason: "expired" };
    return { ok: true, id: row.id };
  }

  /** The address now has an account, so any invitation still waiting for it is spent. */
  async consume(email: string): Promise<void> {
    await this.pool.query("UPDATE auth.invite SET used_at = now() WHERE email = $1 AND used_at IS NULL AND revoked_at IS NULL", [normalizeEmail(email)]);
  }

  /** True when a pending invitation was revoked; false when there was nothing to revoke. */
  async revoke(id: string): Promise<boolean> {
    if (!/^[0-9a-f-]{36}$/i.test(id)) return false;
    const result = await this.pool.query("UPDATE auth.invite SET revoked_at = now() WHERE id = $1 AND used_at IS NULL AND revoked_at IS NULL", [id]);
    return (result.rowCount ?? 0) > 0;
  }

  async list(limit = 200): Promise<Invite[]> {
    const { rows } = await this.pool.query<Row>(`SELECT ${COLUMNS} FROM auth.invite ORDER BY created_at DESC LIMIT $1`, [limit]);
    return rows.map(toInvite);
  }
}
