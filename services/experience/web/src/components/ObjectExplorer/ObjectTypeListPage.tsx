import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Card, Checkbox, HTMLSelect, Tag } from "@blueprintjs/core";
import { useObjectSets, useObjectTypes, useObjectTypeGroups } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { CardGrid } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import { useObjectExplorerFavoritesStore } from "../../store/objectExplorerFavorites";
import { objectSetBrowsePath, urnShortName } from "./objectExplorerUtils";

export function ObjectTypeListPage() {
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const { data: objectSets = [] } = useObjectSets();
  const [groupFilter, setGroupFilter] = useState("");
  const [showEphemeral, setShowEphemeral] = useState(false);
  const favoriteNames = useObjectExplorerFavoritesStore((s) => s.objectTypes);
  const toggleFavorite = useObjectExplorerFavoritesStore((s) => s.toggleObjectType);

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const scopedTypes = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;
  const ephemeralTypeCount = scopedTypes.filter((ot) => isEphemeralTestName(ot.name)).length;
  const visibleTypes = showEphemeral
    ? scopedTypes
    : scopedTypes.filter((ot) => !isEphemeralTestName(ot.name));

  const favorites = useMemo(
    () => data.filter((ot) => favoriteNames.includes(ot.name) && !isEphemeralTestName(ot.name)),
    [data, favoriteNames],
  );

  const browseableSets = useMemo(() => {
    const filtered = objectSets.filter((os) => {
      if (os.visibility === "hidden") return false;
      if (!showEphemeral && isEphemeralTestName(os.name)) return false;
      return true;
    });
    return filtered.sort((a, b) => {
      const rank = (v: string) => (v === "prominent" ? 0 : v === "normal" ? 1 : 2);
      return rank(a.visibility) - rank(b.visibility) || a.name.localeCompare(b.name);
    });
  }, [objectSets, showEphemeral]);

  const ephemeralSetCount = objectSets.filter(
    (os) => os.visibility !== "hidden" && isEphemeralTestName(os.name),
  ).length;
  const ephemeralCount = ephemeralTypeCount + ephemeralSetCount;

  const prominentSets = browseableSets.filter((os) => os.visibility === "prominent").slice(0, 6);

  return (
    <RegistryPage
      title="Objects"
      description={
        <>
          Browse ObjectTypes and Object Sets. Star favorites for quick access. Groups filter the catalog — use the
          group map below to jump between related types.
        </>
      }
      trailing={
        <div className="hl-flex-row hl-items-center hl-gap-sm">
          {ephemeralCount > 0 ? (
            <Checkbox
              checked={showEphemeral}
              label={`Show test leftovers (${ephemeralCount})`}
              onChange={(e) => setShowEphemeral(e.currentTarget.checked)}
              style={{ marginBottom: 0 }}
            />
          ) : null}
          {groups.length > 0 ? (
            <HTMLSelect value={groupFilter} onChange={(e) => setGroupFilter(e.target.value)}>
              <option value="">All groups</option>
              {groups.map((g) => (
                <option key={g.name} value={g.name}>
                  {g.name}
                </option>
              ))}
            </HTMLSelect>
          ) : null}
        </div>
      }
    >
      {favorites.length > 0 && (
        <section className="hl-mb-lg">
          <div className="hl-section-title hl-mb-sm">Favorites</div>
          <CardGrid minWidth={240}>
            {favorites.map((ot) => (
              <ObjectTypeCard
                key={ot.urn}
                name={ot.name}
                description={ot.description}
                classification={ot.classification}
                version={ot.version}
                propertyCount={Object.keys(ot.property_mapping).length}
                titleKey={ot.title_key}
                primaryKey={ot.primary_key}
                propertyPreview={Object.keys(ot.property_mapping).slice(0, 5)}
                favorite
                onToggleFavorite={() => toggleFavorite(ot.name)}
              />
            ))}
          </CardGrid>
        </section>
      )}

      {prominentSets.length > 0 && (
        <section className="hl-mb-lg">
          <div className="hl-section-title hl-mb-sm">Prominent Object Sets</div>
          <CardGrid minWidth={240}>
            {prominentSets.map((os) => {
              const typeName = urnShortName(os.object_type_urn);
              const path = objectSetBrowsePath(typeName, os.name);
              return (
                <Link
                  key={os.urn}
                  to={path.to}
                  params={path.params}
                  search={path.search}
                  className="hl-link-reset"
                >
                  <Card interactive className="hl-h-full">
                    <div className="hl-registry-card-header">
                      <strong className="hl-registry-card-title" title={os.display_name || os.name}>
                        {os.display_name || os.name}
                      </strong>
                      <Tag minimal intent="primary">
                        prominent
                      </Tag>
                    </div>
                    <div className="hl-tag-row hl-mt-xs">
                      <Tag minimal>{typeName}</Tag>
                      <Tag minimal intent={os.lifecycle_status === "active" ? "success" : "none"}>
                        {os.lifecycle_status}
                      </Tag>
                    </div>
                    {os.description && <p className="hl-card-desc">{os.description}</p>}
                  </Card>
                </Link>
              );
            })}
          </CardGrid>
        </section>
      )}

      {groups.length > 0 && (
        <section className="hl-mb-lg">
          <div className="hl-section-title hl-mb-sm">Group map</div>
          <div className="hl-oe-group-map">
            {groups.map((g) => (
              <div
                key={g.name}
                className="hl-oe-group-node"
                data-active={groupFilter === g.name ? "true" : undefined}
              >
                <button
                  type="button"
                  className="hl-oe-group-node-title"
                  onClick={() => setGroupFilter((prev) => (prev === g.name ? "" : g.name))}
                >
                  {g.name}
                  <Tag minimal className="hl-ml-xs">
                    {g.object_types.length}
                  </Tag>
                </button>
                {g.description && <p className="hl-text-muted-sm">{g.description}</p>}
                <div className="hl-tag-row">
                  {g.object_types.map((otName) => (
                    <Link key={otName} to="/objects/$type" params={{ type: otName }}>
                      <Tag minimal interactive intent={favoriteNames.includes(otName) ? "warning" : "none"}>
                        {otName}
                      </Tag>
                    </Link>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </section>
      )}

      {browseableSets.length > 0 && (
        <section className="hl-mb-lg">
          <div className="hl-section-title hl-mb-sm">All Object Sets</div>
          <CardGrid minWidth={240}>
            {browseableSets.map((os) => {
              const typeName = urnShortName(os.object_type_urn);
              const path = objectSetBrowsePath(typeName, os.name);
              return (
                <Link
                  key={os.urn}
                  to={path.to}
                  params={path.params}
                  search={path.search}
                  className="hl-link-reset"
                >
                  <Card interactive className="hl-h-full">
                    <div className="hl-registry-card-header">
                      <strong className="hl-registry-card-title" title={os.display_name || os.name}>
                        {os.display_name || os.name}
                      </strong>
                      {os.visibility === "prominent" && (
                        <Tag minimal intent="primary">
                          prominent
                        </Tag>
                      )}
                    </div>
                    <div className="hl-tag-row hl-mt-xs">
                      <Tag minimal>{typeName}</Tag>
                      <Tag minimal intent={os.lifecycle_status === "active" ? "success" : "none"}>
                        {os.lifecycle_status}
                      </Tag>
                    </div>
                    {os.description && <p className="hl-card-desc">{os.description}</p>}
                  </Card>
                </Link>
              );
            })}
          </CardGrid>
        </section>
      )}

      <div className="hl-section-title hl-mb-sm">
        Object types{activeGroup ? ` · ${activeGroup.name}` : ""}
      </div>
      {visibleTypes.length === 0 && <p className="hl-text-muted">No ObjectTypes in this group.</p>}
      <CardGrid minWidth={260}>
        {visibleTypes.map((ot) => (
          <ObjectTypeCard
            key={ot.urn}
            name={ot.name}
            description={ot.description}
            classification={ot.classification}
            version={ot.version}
            propertyCount={Object.keys(ot.property_mapping).length}
            titleKey={ot.title_key}
            primaryKey={ot.primary_key}
            propertyPreview={Object.keys(ot.property_mapping).slice(0, 5)}
            favorite={favoriteNames.includes(ot.name)}
            onToggleFavorite={() => toggleFavorite(ot.name)}
          />
        ))}
      </CardGrid>
    </RegistryPage>
  );
}

function ObjectTypeCard({
  name,
  description,
  classification,
  version,
  propertyCount,
  titleKey,
  primaryKey,
  propertyPreview,
  favorite,
  onToggleFavorite,
}: {
  name: string;
  description?: string | null;
  classification: string;
  version: number;
  propertyCount: number;
  titleKey?: string | null;
  primaryKey?: string | null;
  propertyPreview: string[];
  favorite: boolean;
  onToggleFavorite: () => void;
}) {
  return (
    <Card interactive className="hl-h-full hl-oe-ot-card">
      <div className="hl-registry-card-header">
        <Link to="/objects/$type" params={{ type: name }} className="hl-link-reset hl-oe-ot-card-link">
          <strong className="hl-registry-card-title" title={name}>
            {name}
          </strong>
        </Link>
        <div className="hl-flex-row hl-items-center hl-gap-xs">
          <Button
            minimal
            small
            icon={favorite ? "star" : "star-empty"}
            intent={favorite ? "warning" : "none"}
            aria-label={favorite ? "Remove favorite" : "Add favorite"}
            onClick={(e) => {
              e.preventDefault();
              e.stopPropagation();
              onToggleFavorite();
            }}
          />
          <ClassificationBadge classification={classification} />
        </div>
      </div>
      <Link to="/objects/$type" params={{ type: name }} className="hl-link-reset">
        {description && <p className="hl-card-desc">{description}</p>}
        <div className="hl-mono hl-text-muted-sm hl-mt-sm">
          v{version} · {propertyCount} properties
          {titleKey ? ` · title:${titleKey}` : primaryKey ? ` · pk:${primaryKey}` : ""}
        </div>
        {propertyPreview.length > 0 && (
          <div className="hl-tag-row hl-mt-sm">
            {propertyPreview.map((p) => (
              <Tag key={p} minimal className="hl-mono">
                {p}
              </Tag>
            ))}
            {propertyCount > propertyPreview.length && (
              <Tag minimal>+{propertyCount - propertyPreview.length}</Tag>
            )}
          </div>
        )}
      </Link>
    </Card>
  );
}
