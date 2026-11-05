import { engineOr } from "@/lib/engine";
import { ErrorNote, PageHeader } from "@/components/ui";
import { ProjectsClient } from "./projects-client";

export const dynamic = "force-dynamic";

export default async function ProjectsPage() {
  const { data, error } = await engineOr<any[]>("/projects", []);
  return (
    <>
      <PageHeader title="Projects" />
      <ErrorNote error={error} />
      <ProjectsClient initial={data} />
    </>
  );
}
