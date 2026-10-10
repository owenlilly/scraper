import type { Metadata } from "next";
import Link from "next/link";
import { headers } from "next/headers";
import { AdminPanel, type InviteRow, type PersonRow } from "../../components/admin/AdminPanel";
import { getServices } from "../../lib/services";
import { requireAdmin } from "../../lib/session";

export const metadata: Metadata = { title: "Invitations and people" };

export default async function AdminPage() {
  const session = await requireAdmin();
  const { invites, auth } = getServices();
  const [waiting, listed] = await Promise.all([invites.list(), auth.api.listUsers({ headers: await headers(), query: { limit: 200, sortBy: "createdAt", sortDirection: "desc" } })]);

  const inviteRows: InviteRow[] = waiting.map((invite) => ({ id: invite.id, email: invite.email, status: invite.status, createdAt: invite.createdAt.toISOString() }));
  const people: PersonRow[] = listed.users.map((user) => ({ id: user.id, name: user.name, email: user.email, role: user.role ?? null, banned: user.banned === true }));
  return (
    <div className="page">
      <header className="topbar">
        <Link className="wordmark" href="/">
          Page Chat
        </Link>
        <Link className="btn btn-quiet" href="/">
          Back to workspace
        </Link>
      </header>
      <AdminPanel invites={inviteRows} people={people} selfId={session.user.id} />
    </div>
  );
}
