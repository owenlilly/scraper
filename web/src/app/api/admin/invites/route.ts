import { adminInviteHandlers } from "../../../../lib/admin-invites";
import { getServices } from "../../../../lib/services";

export const dynamic = "force-dynamic";

export const GET = (request: Request) => adminInviteHandlers(getServices()).list(request);
export const POST = (request: Request) => adminInviteHandlers(getServices()).create(request);
