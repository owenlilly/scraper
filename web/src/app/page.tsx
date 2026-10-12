import { Workspace } from "../components/workspace/Workspace";
import { WorkspaceProviders } from "../components/workspace/ApiProvider";
import { getServices } from "../lib/services";
import { requireSession } from "../lib/session";

export default async function Home() {
  const session = await requireSession();
  const user = session.user as { name: string; email: string; role?: string | null };
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <Workspace person={{ name: user.name, email: user.email, isAdmin: user.role === "admin" }} />
    </WorkspaceProviders>
  );
}
