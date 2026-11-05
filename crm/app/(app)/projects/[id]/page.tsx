import { engineOr } from "@/lib/engine";
import { ErrorNote } from "@/components/ui";
import { ProjectView } from "./project-view";

export const dynamic = "force-dynamic";

export default async function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const { data, error } = await engineOr<any>(`/projects/${id}`, null);
  if (!data) return <ErrorNote error={error || "project not found"} />;
  return <ProjectView initial={data} />;
}
