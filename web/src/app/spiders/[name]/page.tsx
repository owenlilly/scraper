import type { Metadata } from "next";
import { SpiderPage } from "../../../components/spiders/SpiderPage";
import { SpidersFrame } from "../../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../../components/workspace/ApiProvider";
import { getServices } from "../../../lib/services";
import { requireAdmin } from "../../../lib/session";

type Props = { params: Promise<{ name: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { name } = await params;
  return { title: name };
}

export default async function SpiderRoute({ params }: Props) {
  await requireAdmin();
  const { name } = await params;
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <SpiderPage name={name} />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
