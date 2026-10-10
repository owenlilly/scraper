import { adminInviteHandlers } from "../../../../../lib/admin-invites";
import { getServices } from "../../../../../lib/services";

export const dynamic = "force-dynamic";

export async function DELETE(request: Request, context: { params: Promise<{ id: string }> }) {
  const { id } = await context.params;
  return adminInviteHandlers(getServices()).revoke(request, id);
}
