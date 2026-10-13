import type { Metadata } from "next";
import { SpiderTable } from "../../components/spiders/SpiderTable";
import { SpidersFrame } from "../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../components/workspace/ApiProvider";
import { getServices } from "../../lib/services";
import { requireAdmin } from "../../lib/session";

export const metadata: Metadata = { title: "Spiders" };

export default async function SpidersPage() {
  await requireAdmin();
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <SpiderTable />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
