import { getServices } from "../../../../lib/services";

export const dynamic = "force-dynamic";

// Every method Better Auth uses. Services are built on first request, so `next build` needs no secrets.
const handler = (request: Request) => getServices().auth.handler(request);
export { handler as GET, handler as POST, handler as PUT, handler as PATCH, handler as DELETE };
