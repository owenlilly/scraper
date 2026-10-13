import type { Metadata } from "next";
import Link from "next/link";
import { SettingsForm } from "../../../../components/spiders/SettingsForm";
import { SpidersFrame } from "../../../../components/spiders/SpidersFrame";
import { WorkspaceProviders } from "../../../../components/workspace/ApiProvider";
import { getServices } from "../../../../lib/services";
import { requireAdmin } from "../../../../lib/session";

type Props = { params: Promise<{ name: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { name } = await params;
  return { title: `${name} settings` };
}

export default async function SpiderSettingsRoute({ params }: Props) {
  await requireAdmin();
  const { name } = await params;
  return (
    <WorkspaceProviders apiUrl={getServices().env.apiUrl}>
      <SpidersFrame>
        <div>
          <Link className="sp-back small" href={`/spiders/${name}`}>
            {name}
          </Link>
          <h1>Settings</h1>
          <p className="sp-lede">What shows in this spider's results, how its crawls are limited, and where it can run. Changes apply to the next run you start.</p>
        </div>
        <SettingsForm name={name} />
      </SpidersFrame>
    </WorkspaceProviders>
  );
}
