import { useMemo, useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Card, Checkbox, FormGroup, HTMLSelect, InputGroup, Tab, Tabs, Tag, type TabId } from "@blueprintjs/core";
import { useObjectSets, useObjectTypes, useObjectTypeGroups } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { CardGrid, EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";
import { useObjectExplorerFavoritesStore } from "../../store/objectExplorerFavorites";
import { objectSetBrowsePath, urnShortName } from "./objectExplorerUtils";
import type { ObjectSet } from "../../api/knowledge";

function matchesQuery(needle: string, name: string, extra?: string | null) {
  if (!needle) return true;
  return name.toLowerCase().includes(needle) || (extra?.toLowerCase().includes(needle) ?? false);
}

export function ObjectTypeListPage() {
  const { data } = useObjectTypes();
  const { data: groups } = useObjectTypeGroups();
  const { data: objectSets = [] } = useObjectSets();
  const [groupFilter, setGroupFilter] = useState("");
  const [query, setQuery] = useState("");
  const [showEphemeral, setShowEphemeral] = useState(false);
  const [catalogTab, setCatalogTab] = useState<TabId>("types");
  const favoriteNames = useObjectExplorerFavoritesStore((s) => s.objectTypes);
  const toggleFavorite = useObjectExplorerFavoritesStore((s) => s.toggleObjectType);

  const activeGroup = groups.find((g) => g.name === groupFilter);
  const needle = query.trim().toLowerCase();

  const scopedTypes = activeGroup ? data.filter((ot) => activeGroup.object_types.includes(ot.name)) : data;
  const ephemeralTypeCount = scopedTypes.filter((ot) => isEphemeralTestName(ot.name)).length;
  const visibleTypes = (showEphemeral ? scopedTypes : scopedTypes.filter((ot) => !isEphemeralTestName(ot.name))).filter(
    (ot) => matchesQuery(needle, ot.name, ot.description),
  );

  const favorites = useMemo(
    () =>
      data.filter(
        (ot) =>
          favoriteNames.includes(ot.name) &&
          !isEphemeralTestName(ot.name) &&
          matchesQuery(needle, ot.name, ot.description),
      ),
    [data, favoriteNames, needle],
  );

  const browseableSets = useMemo(() => {
    const filtered = objectSets.filter((os) => {
      if (os.visibility === "hidden") return false;
      if (!showEphemeral && isEphemeralTestName(os.name)) return false;
      const typeName = urnShortName(os.object_type_urn);
      return (
        matchesQuery(needle, os.display_name || os.name, os.description) || matchesQuery(needle, os.name, typeName)
      );
    });
    return filtered.sort((a, b) => {
      const rank = (v: string) => (v === "prominent" ? 0 : v === "normal" ? 1 : 2);
      return rank(a.visibility) - rank(b.visibility) || a.name.localeCompare(b.name);
    });
  }, [objectSets, showEphemeral, needle]);

  const ephemeralSetCount = objectSets.filter(
    (os) => os.visibility !== "hidden" && isEphemeralTestName(os.name),
  ).length;
  const ephemeralCount = ephemeralTypeCount + ephemeralSetCount;
  const hasSetsCatalog = objectSets.some(
    (os) => os.visibility !== "hidden" && (showEphemeral || !isEphemeralTestName(os.name)),
  );

  const typesPanel = (
    <>
      {groups.length > 0 && !needle && (
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
      {activeGroup && <div className="hl-text-muted-sm hl-mb-sm">Group · {activeGroup.name}</div>}
      {visibleTypes.length === 0 && (
        <EmptyState>
          {needle
            ? browseableSets.length > 0
              ? `No types match “${query.trim()}”. ${browseableSets.length} object sets do — switch tabs.`
              : `No types or sets match “${query.trim()}”.`
            : activeGroup
              ? "No object types in this group."
              : "No object types yet."}
        </EmptyState>
      )}
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
    </>
  );

  const setsPanel = (
    <>
      {browseableSets.length === 0 && (
        <EmptyState>
          {needle
            ? visibleTypes.length > 0
              ? `No object sets match “${query.trim()}”. ${visibleTypes.length} types do — switch tabs.`
              : `No types or sets match “${query.trim()}”.`
            : "No object sets yet."}
        </EmptyState>
      )}
      <CardGrid minWidth={240}>
        {browseableSets.map((os) => (
          <ObjectSetCard key={os.urn} objectSet={os} />
        ))}
      </CardGrid>
    </>
  );

  return (
    <RegistryPage
      title="Objects"
      description="Browse object types and saved sets. Star the ones you use often — groups help you jump between related types."
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
          {groups.length > 0 && catalogTab !== "sets" ? (
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
      <FormGroup label="Filter this list" labelFor="objects-filter" className="hl-list-filter">
        <InputGroup
          id="objects-filter"
          leftIcon="filter"
          placeholder="Name or description…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          autoComplete="off"
          spellCheck={false}
        />
      </FormGroup>
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

      {hasSetsCatalog ? (
        <Tabs
          id="objects-catalog"
          className="hl-oe-catalog-tabs"
          selectedTabId={catalogTab}
          onChange={setCatalogTab}
          renderActiveTabPanelOnly
        >
          <Tab
            id="types"
            title={
              <span className="hl-oe-tab-title">
                Object types
                <Tag minimal round>
                  {visibleTypes.length}
                </Tag>
              </span>
            }
            panel={typesPanel}
          />
          <Tab
            id="sets"
            title={
              <span className="hl-oe-tab-title">
                Object sets
                <Tag minimal round>
                  {browseableSets.length}
                </Tag>
              </span>
            }
            panel={setsPanel}
          />
        </Tabs>
      ) : (
        typesPanel
      )}
    </RegistryPage>
  );
}

function ObjectSetCard({ objectSet }: { objectSet: ObjectSet }) {
  const typeName = urnShortName(objectSet.object_type_urn);
  const path = objectSetBrowsePath(typeName, objectSet.name);
  const title = objectSet.display_name || objectSet.name;
  return (
    <Link to={path.to} params={path.params} search={path.search} className="hl-link-reset">
      <Card interactive className="hl-h-full">
        <div className="hl-registry-card-header">
          <strong className="hl-registry-card-title" title={title}>
            {title}
          </strong>
          {objectSet.visibility === "prominent" && (
            <Tag minimal intent="primary">
              prominent
            </Tag>
          )}
        </div>
        <div className="hl-tag-row hl-mt-xs">
          <Tag minimal>{typeName}</Tag>
          <Tag minimal intent={objectSet.lifecycle_status === "active" ? "success" : "none"}>
            {objectSet.lifecycle_status}
          </Tag>
        </div>
        {objectSet.description && <p className="hl-card-desc">{objectSet.description}</p>}
      </Card>
    </Link>
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
