import { Button, Card, Tag } from "@blueprintjs/core";
import { Link, useNavigate } from "@tanstack/react-router";
import { useObjectTypeGroups, useObjectTypes } from "../../api/hooks";
import {
  useOntologyDiscoverStore,
  type OntologyRecentItem,
  type OntologyResourceKind,
} from "../../store/ontologyDiscover";
import { isEphemeralTestName } from "./ephemeralResources";
import { OntologyTabHeader } from "./OntologyTabLayout";

function hrefFor(item: Pick<OntologyRecentItem, "kind" | "name">): {
  to: string;
  params?: Record<string, string>;
  search?: Record<string, string>;
} {
  switch (item.kind) {
    case "object_type":
      return { to: "/ontology/object-types/$name", params: { name: item.name } };
    case "relation_type":
      return { to: "/ontology/relation-types/$name", params: { name: item.name } };
    case "action_type":
      return { to: "/ontology/action-types/$name", params: { name: item.name } };
    case "interface":
      return { to: "/ontology", search: { tab: "interfaces" } };
    case "value_type":
      return { to: "/ontology", search: { tab: "value-types" } };
    case "shared_property_type":
      return { to: "/ontology", search: { tab: "shared-property-types" } };
    default:
      return { to: "/ontology" };
  }
}

function kindLabel(kind: OntologyResourceKind): string {
  switch (kind) {
    case "object_type":
      return "ObjectType";
    case "relation_type":
      return "RelationType";
    case "action_type":
      return "Action";
    case "interface":
      return "Interface";
    case "value_type":
      return "ValueType";
    case "shared_property_type":
      return "SPT";
    default:
      return kind;
  }
}

function ResourceChip({ item }: { item: OntologyRecentItem }) {
  const navigate = useNavigate();
  const isFavorite = useOntologyDiscoverStore((s) => s.isFavorite(item.kind, item.name));
  const toggleFavorite = useOntologyDiscoverStore((s) => s.toggleFavorite);
  const href = hrefFor(item);

  return (
    <Card className="hl-om-discover-chip">
      <div className="hl-flex-between hl-items-center">
        <button
          type="button"
          className="hl-om-discover-chip-main"
          onClick={() =>
            void navigate({
              to: href.to,
              params: href.params as never,
              search: (href.search ?? {}) as never,
            })
          }
        >
          <Tag minimal>{kindLabel(item.kind)}</Tag>
          <strong className="hl-mono">{item.name}</strong>
        </button>
        <Button
          minimal
          small
          icon={isFavorite ? "star" : "star-empty"}
          intent={isFavorite ? "warning" : "none"}
          title={isFavorite ? "Remove favorite" : "Add favorite"}
          onClick={() => toggleFavorite(item.kind, item.name)}
        />
      </div>
    </Card>
  );
}

/** Foundry Discover lite — favorites, recently viewed, prominent OTs, groups. */
export function OntologyDiscoverTab() {
  const navigate = useNavigate();
  const favorites = useOntologyDiscoverStore((s) => s.favorites);
  const recent = useOntologyDiscoverStore((s) => s.recent);
  const { data: objectTypes = [] } = useObjectTypes();
  const { data: groups = [] } = useObjectTypeGroups();

  const prominent = objectTypes
    .filter((ot) => ot.visibility === "prominent" && !isEphemeralTestName(ot.name))
    .slice(0, 8);
  const fallbackRecentOt = objectTypes
    .filter((ot) => !isEphemeralTestName(ot.name))
    .slice(0, 6)
    .map((ot): OntologyRecentItem => ({ kind: "object_type", name: ot.name, visitedAt: 0 }));

  return (
    <div className="hl-om-discover">
      <OntologyTabHeader
        description={
          <>
            Discover your Ontology — favorites, recently opened resources, prominent ObjectTypes, and groups.
            Star resources from detail pages or here.
          </>
        }
      />

      <section className="hl-om-discover-section">
        <h4 className="hl-section-title">Favorites</h4>
        {favorites.length === 0 ? (
          <p className="hl-text-muted">No favorites yet — star an ObjectType, RelationType, or Action Type.</p>
        ) : (
          <div className="hl-om-discover-grid">
            {favorites.map((item) => (
              <ResourceChip key={`fav-${item.kind}-${item.name}`} item={item} />
            ))}
          </div>
        )}
      </section>

      <section className="hl-om-discover-section">
        <h4 className="hl-section-title">Recently viewed</h4>
        {recent.length === 0 ? (
          <div className="hl-om-discover-grid">
            {fallbackRecentOt.map((item) => (
              <ResourceChip key={`seed-${item.name}`} item={item} />
            ))}
          </div>
        ) : (
          <div className="hl-om-discover-grid">
            {recent.map((item) => (
              <ResourceChip key={`rec-${item.kind}-${item.name}`} item={item} />
            ))}
          </div>
        )}
      </section>

      {prominent.length > 0 && (
        <section className="hl-om-discover-section">
          <h4 className="hl-section-title">Prominent ObjectTypes</h4>
          <div className="hl-om-discover-grid">
            {prominent.map((ot) => (
              <ResourceChip
                key={`prom-${ot.name}`}
                item={{ kind: "object_type", name: ot.name, visitedAt: 0 }}
              />
            ))}
          </div>
        </section>
      )}

      <section className="hl-om-discover-section">
        <div className="hl-flex-between hl-items-center">
          <h4 className="hl-section-title" style={{ margin: 0 }}>
            Object Type Groups
          </h4>
          <Button
            minimal
            small
            onClick={() => void navigate({ to: "/ontology", search: { tab: "object-type-groups" } })}
          >
            Manage groups
          </Button>
        </div>
        {groups.length === 0 ? (
          <p className="hl-text-muted hl-mt-sm">No groups yet.</p>
        ) : (
          <div className="hl-om-discover-grid hl-mt-sm">
            {groups.map((g) => (
              <Card key={g.name} className="hl-om-discover-chip">
                <strong>{g.name}</strong>
                <p className="hl-text-muted-sm hl-mb-sm">
                  {g.object_types.length} ObjectType{g.object_types.length === 1 ? "" : "s"}
                </p>
                <div className="hl-tag-row">
                  {g.object_types.slice(0, 6).map((otName) => (
                    <Link
                      key={otName}
                      to="/ontology/object-types/$name"
                      params={{ name: otName }}
                      className="hl-link-accent hl-mono"
                    >
                      {otName}
                    </Link>
                  ))}
                  {g.object_types.length > 6 ? (
                    <span className="hl-text-muted-sm">+{g.object_types.length - 6}</span>
                  ) : null}
                </div>
              </Card>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
