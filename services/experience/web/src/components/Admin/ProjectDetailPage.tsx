import { useParams } from "@tanstack/react-router";
import { Button, Tag } from "@blueprintjs/core";
import {
  useProjects,
  useProjectPins,
  usePinResource,
  useUnpinResource,
  useObjectTypes,
  useApplications,
} from "../../api/hooks";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { ResourceActionsMenu, ResourceTagBadges } from "../common/ResourceActionsMenu";
import { DetailPage, PageSection } from "../common/PageLayout";
import type { Project } from "../../api/identity";

function PinToggle({ projectUrn, resourceUrn, pinned }: { projectUrn: string; resourceUrn: string; pinned: boolean }) {
  const pin = usePinResource(projectUrn);
  const unpin = useUnpinResource(projectUrn);
  const pending = pin.isPending || unpin.isPending;
  return (
    <Button
      small
      minimal
      icon="pin"
      intent={pinned ? "warning" : "none"}
      style={{ opacity: pinned ? 1 : 0.5 }}
      disabled={pending}
      onClick={() => (pinned ? unpin.mutate(resourceUrn) : pin.mutate(resourceUrn))}
      title={pinned ? "Unpin" : "Pin in this project"}
    />
  );
}

export function ProjectDetailPage() {
  const { name } = useParams({ from: "/shell/admin/projects/$name" });
  const { data: projects } = useProjects();
  const project = projects.find((p) => p.name === name);

  if (!project) return <p className="hl-text-muted">No project named "{name}".</p>;

  return <ProjectDetailContent project={project} />;
}

function ProjectDetailContent({ project }: { project: Project }) {
  const { data: pins } = useProjectPins(project.urn);
  const { data: objectTypes } = useObjectTypes();
  const { data: applications } = useApplications();

  const pinnedUrns = new Set(pins.map((p) => p.resource_urn));
  const scopedObjectTypes = objectTypes.filter((ot) => ot.project_urn === project.urn);
  const scopedApplications = applications.filter((a) => a.project_urn === project.urn);
  const pinnedItems = [
    ...scopedObjectTypes.filter((ot) => pinnedUrns.has(ot.urn)).map((ot) => ({ urn: ot.urn, label: ot.name, kind: "Object type" })),
    ...scopedApplications.filter((a) => pinnedUrns.has(a.urn)).map((a) => ({ urn: a.urn, label: a.name, kind: "Application" })),
  ];

  return (
    <DetailPage
      breadcrumbs={[{ label: "Admin", to: "/admin" }, { label: project.name }]}
      title={project.name}
      description={
        <>
          Resources scoped into this project — ObjectTypes via their propose/publish workflow, Applications directly.
          Pinning surfaces a resource here regardless of how much else the project accumulates.
        </>
      }
    >
      <PageSection title="Pinned">
        <CardGrid minWidth={240}>
          {pinnedItems.map((item) => (
            <div key={item.urn} className="hl-panel hl-flex-between hl-items-start">
              <div>
                <Tag minimal>{item.kind}</Tag>
                <div className="hl-mt-xs" style={{ fontWeight: 600 }}>
                  {item.label}
                </div>
                <ResourceTagBadges urn={item.urn} />
              </div>
              <div className="hl-flex-row hl-gap-xs">
                <PinToggle projectUrn={project.urn} resourceUrn={item.urn} pinned />
                <ResourceActionsMenu urn={item.urn} />
              </div>
            </div>
          ))}
          {pinnedItems.length === 0 && <EmptyState>Nothing pinned yet — pin from any scoped resource below.</EmptyState>}
        </CardGrid>
      </PageSection>

      <PageSection title="Object types in this project">
        <CardGrid minWidth={240}>
          {scopedObjectTypes.map((ot) => (
            <div key={ot.urn} className="hl-panel hl-flex-between hl-items-start">
              <div>
                <div style={{ fontWeight: 600 }}>{ot.name}</div>
                <ResourceTagBadges urn={ot.urn} />
              </div>
              <div className="hl-flex-row hl-gap-xs">
                <PinToggle projectUrn={project.urn} resourceUrn={ot.urn} pinned={pinnedUrns.has(ot.urn)} />
                <ResourceActionsMenu urn={ot.urn} />
              </div>
            </div>
          ))}
          {scopedObjectTypes.length === 0 && <EmptyState>No ObjectTypes scoped to this project.</EmptyState>}
        </CardGrid>
      </PageSection>

      <PageSection title="Applications in this project">
        <CardGrid minWidth={240}>
          {scopedApplications.map((a) => (
            <div key={a.urn} className="hl-panel hl-flex-between hl-items-start">
              <div>
                <div style={{ fontWeight: 600 }}>{a.name}</div>
                <Tag minimal intent={a.status === "promoted" ? "success" : "warning"} className="hl-mt-xs">
                  {a.status}
                </Tag>
                <ResourceTagBadges urn={a.urn} />
              </div>
              <div className="hl-flex-row hl-gap-xs">
                <PinToggle projectUrn={project.urn} resourceUrn={a.urn} pinned={pinnedUrns.has(a.urn)} />
                <ResourceActionsMenu urn={a.urn} />
              </div>
            </div>
          ))}
          {scopedApplications.length === 0 && <EmptyState>No Applications scoped to this project.</EmptyState>}
        </CardGrid>
      </PageSection>
    </DetailPage>
  );
}
