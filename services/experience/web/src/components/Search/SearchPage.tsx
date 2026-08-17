import { useMemo, useState } from "react";
import { Link, useNavigate, useSearch as useSearchParams } from "@tanstack/react-router";
import { Button, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { useCreateObjectSet, useInterfaces, useObjectTypes, useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { RegistryDialog } from "../common/RegistryDialog";
import { SearchResultsSkeleton } from "../common/Skeleton";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { objectSetBrowsePath, parseSearchHitRef, preferredSearchProperty } from "../ObjectExplorer/objectExplorerUtils";
import { expandFilterPropertyKeys } from "../Ontology/objectSetPredicates";
import { isEphemeralTestName } from "../Ontology/ephemeralResources";

const PAGE_SIZE = 20;

export function SearchPage() {
  const navigate = useNavigate();
  const { q: prefill } = useSearchParams({ strict: false });
  const [query, setQuery] = useState(prefill ?? "");
  const [submitted, setSubmitted] = useState(prefill ?? "");
  const [objectType, setObjectType] = useState<string | undefined>(undefined);
  const [interfaceName, setInterfaceName] = useState<string | undefined>(undefined);
  const [propFilters, setPropFilters] = useState<Record<string, string>>({});
  const [page, setPage] = useState(0);
  const [savingSet, setSavingSet] = useState(false);
  const [setName, setSetName] = useState("");
  const [structFilterProp, setStructFilterProp] = useState("");
  const [structFilterValue, setStructFilterValue] = useState("");

  const { data: objectTypes } = useObjectTypes();
  const { data: interfaces = [] } = useInterfaces();
  const createObjectSet = useCreateObjectSet();
  const { data, isLoading } = useSearch(submitted, {
    objectType,
    interface: interfaceName,
    from: page * PAGE_SIZE,
    size: PAGE_SIZE,
    propFilters,
  });

  const selectedOt = useMemo(
    () => (objectTypes ?? []).find((ot) => ot.name === objectType),
    [objectTypes, objectType],
  );
  const saveProperty = preferredSearchProperty(selectedOt);
  const structFieldKeys = useMemo(
    () =>
      expandFilterPropertyKeys(selectedOt?.property_mapping, selectedOt?.property_types).filter((k) =>
        k.includes("."),
      ),
    [selectedOt],
  );
  const durableInterfaces = useMemo(
    () => interfaces.filter((iface) => !isEphemeralTestName(iface.name)),
    [interfaces],
  );
  const ephemeralInterfaceCount = interfaces.length - durableInterfaces.length;

  function submit(next: string) {
    setSubmitted(next);
    setObjectType(undefined);
    setInterfaceName(undefined);
    setPropFilters({});
    setStructFilterProp("");
    setStructFilterValue("");
    setPage(0);
  }

  function toggleFacet(facet: string) {
    setObjectType((current) => (current === facet ? undefined : facet));
    setPropFilters({});
    setStructFilterProp("");
    setStructFilterValue("");
    setPage(0);
  }

  function applyStructFilter() {
    if (!structFilterProp || !structFilterValue.trim()) return;
    setPropFilters((current) => ({ ...current, [structFilterProp]: structFilterValue.trim() }));
    setPage(0);
  }

  function togglePropFacet(prop: string, value: string) {
    setPropFilters((current) => {
      if (current[prop] === value) {
        const next = { ...current };
        delete next[prop];
        return next;
      }
      return { ...current, [prop]: value };
    });
    setPage(0);
  }

  const {
    submit: submitSaveSet,
    error: saveError,
    isPending: savePending,
  } = useAsyncAction(async () => {
    if (!objectType || !setName.trim() || !saveProperty) return;
    const name = setName.trim();
    await createObjectSet.mutateAsync({
      name,
      object_type: objectType,
      display_name: name,
      description: submitted
        ? `Saved from search: “${submitted}” on ${saveProperty}`
        : `All ${objectType} instances`,
      lifecycle_status: "experimental",
      visibility: "normal",
      definition: submitted
        ? { all: [{ property: saveProperty, op: "contains", value: submitted }] }
        : { all: [] },
    });
    setSavingSet(false);
    setSetName("");
    const path = objectSetBrowsePath(objectType, name);
    void navigate({ to: path.to, params: path.params, search: path.search });
  }, { successMessage: `Object set "${setName.trim()}" created` });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const facetEntries = Object.entries(data?.facets ?? {}).sort((a, b) => b[1] - a[1]);
  const propertyFacetEntries = Object.entries(data?.property_facets ?? {});
  const results = data?.results ?? [];

  return (
    <RegistryPage
      title="Search"
      description={
        <>
          Unified search (Knowledge `/search`) — entitlement tokens filter at the source; ObjectType, interface, and
          property facets narrow hits via OpenSearch <code>post_filter</code> (totals reflect the active filters).
        </>
      }
      actions={
        objectType ? (
          <Button
            icon="floppy-disk"
            disabled={!saveProperty}
            onClick={() => {
              setSetName("");
              setSavingSet(true);
            }}
          >
            Save as Object Set
          </Button>
        ) : undefined
      }
    >
      <div className="hl-flex-row hl-gap-sm hl-items-start" style={{ flexWrap: "wrap" }}>
        <InputGroup
          large
          leftIcon="search"
          placeholder="Search..."
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && submit(query)}
          style={{ flex: "1 1 280px" }}
        />
        <FormGroup label="Interface" style={{ marginBottom: 0, minWidth: 200 }}>
          <HTMLSelect
            large
            fill
            value={interfaceName ?? ""}
            onChange={(e) => {
              setInterfaceName(e.target.value || undefined);
              setPage(0);
            }}
          >
            <option value="">Any interface</option>
            {durableInterfaces.map((iface) => (
              <option key={iface.name} value={iface.name}>
                {iface.name}
              </option>
            ))}
            {ephemeralInterfaceCount > 0 && interfaceName && isEphemeralTestName(interfaceName) ? (
              <option value={interfaceName}>{interfaceName} (test)</option>
            ) : null}
          </HTMLSelect>
        </FormGroup>
      </div>

      {isLoading && submitted && <SearchResultsSkeleton />}

      {data && !isLoading && (
        <div className="hl-search-layout">
          {facetEntries.length > 0 && (
            <div className="hl-search-facets">
              <div className="hl-section-title hl-mb-sm">Object type</div>
              <div className="hl-grid-gap-sm">
                {facetEntries.map(([facet, count]) => (
                  <button
                    key={facet}
                    type="button"
                    className="hl-facet-item"
                    data-active={objectType === facet}
                    onClick={() => toggleFacet(facet)}
                  >
                    <span>{facet}</span>
                    <span className="hl-text-muted">{count}</span>
                  </button>
                ))}
              </div>
              {propertyFacetEntries.map(([prop, buckets]) => (
                <div key={prop} className="hl-mt-md">
                  <div className="hl-section-title hl-mb-sm">{prop}</div>
                  <div className="hl-grid-gap-sm">
                    {Object.entries(buckets)
                      .sort((a, b) => b[1] - a[1])
                      .map(([value, count]) => (
                        <button
                          key={`${prop}-${value}`}
                          type="button"
                          className="hl-facet-item"
                          data-active={propFilters[prop] === value}
                          onClick={() => togglePropFacet(prop, value)}
                        >
                          <span>{value}</span>
                          <span className="hl-text-muted">{count}</span>
                        </button>
                      ))}
                  </div>
                </div>
              ))}
            </div>
          )}

          <div className="hl-flex-1 hl-min-w-0">
            <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-md">
              <Tag minimal>{data.total} results</Tag>
              {interfaceName && (
                <Tag
                  minimal
                  intent="primary"
                  icon="diagram-tree"
                  onRemove={() => {
                    setInterfaceName(undefined);
                    setPage(0);
                  }}
                >
                  interface:{interfaceName}
                </Tag>
              )}
              {objectType && (
                <Tag minimal intent="primary" onRemove={() => toggleFacet(objectType)}>
                  {objectType}
                </Tag>
              )}
              {Object.entries(propFilters).map(([prop, value]) => (
                <Tag
                  key={prop}
                  minimal
                  intent="primary"
                  className="hl-mono"
                  onRemove={() => {
                    setPropFilters((current) => {
                      const next = { ...current };
                      delete next[prop];
                      return next;
                    });
                    setPage(0);
                  }}
                >
                  {prop}={value}
                </Tag>
              ))}
              {objectType && !saveProperty && (
                <span className="hl-text-muted-sm">Select a type with properties to save as an Object Set.</span>
              )}
            </div>

            {objectType && structFieldKeys.length > 0 && (
              <div className="hl-flex-row hl-gap-sm hl-items-end hl-mb-md" style={{ flexWrap: "wrap" }}>
                <FormGroup label="Struct field" style={{ marginBottom: 0, minWidth: 180 }}>
                  <HTMLSelect
                    fill
                    value={structFilterProp}
                    onChange={(e) => setStructFilterProp(e.target.value)}
                  >
                    <option value="">Select field…</option>
                    {structFieldKeys.map((key) => (
                      <option key={key} value={key}>
                        {key}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Equals" style={{ marginBottom: 0, minWidth: 160 }}>
                  <InputGroup
                    value={structFilterValue}
                    onChange={(e) => setStructFilterValue(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && applyStructFilter()}
                    placeholder="Paris"
                  />
                </FormGroup>
                <Button
                  icon="filter"
                  disabled={!structFilterProp || !structFilterValue.trim()}
                  onClick={applyStructFilter}
                >
                  Filter
                </Button>
              </div>
            )}

            {results.map((r) => {
              const ref = parseSearchHitRef(r);
              const body = (
                <>
                  <div className="hl-flex-between">
                    <span className="hl-mono hl-text-muted-sm">{r.urn}</span>
                    <ClassificationBadge classification={r.classification} />
                  </div>
                  <p className="hl-body-text hl-mt-sm" style={{ marginBottom: 0 }}>
                    {r.text}
                  </p>
                  {ref && (
                    <div className="hl-text-muted-sm hl-mt-xs">
                      Open {ref.type}/{ref.id}
                    </div>
                  )}
                </>
              );
              if (!ref) {
                return (
                  <div key={r.urn} className="hl-panel hl-mb-sm">
                    {body}
                  </div>
                );
              }
              return (
                <Link
                  key={r.urn}
                  to="/objects/$type/$id"
                  params={{ type: ref.type, id: ref.id }}
                  className="hl-panel hl-mb-sm hl-search-result hl-link-reset"
                >
                  {body}
                </Link>
              );
            })}
            {results.length === 0 && (
              <EmptyState>No results for "{submitted}".</EmptyState>
            )}

            {totalPages > 1 && (
              <div className="hl-flex-row hl-items-center hl-gap-md hl-mt-md">
                <Button minimal small icon="chevron-left" disabled={page === 0} onClick={() => setPage((p) => p - 1)} />
                <span className="hl-text-muted">
                  Page {page + 1} of {totalPages}
                </span>
                <Button
                  minimal
                  small
                  icon="chevron-right"
                  disabled={page + 1 >= totalPages}
                  onClick={() => setPage((p) => p + 1)}
                />
              </div>
            )}
          </div>
        </div>
      )}

      {!submitted && !isLoading && (
        <p className="hl-text-muted hl-mt-md">Enter a query to search across indexed objects.</p>
      )}

      <RegistryDialog
        isOpen={savingSet}
        title="Save search as Object Set"
        onClose={() => setSavingSet(false)}
        error={saveError}
        isPending={savePending}
        submitLabel="Create & browse"
        submitDisabled={!setName.trim() || !objectType || !saveProperty}
        onSubmit={() => submitSaveSet(undefined)}
      >
        <p className="hl-text-muted-sm hl-mb-md">
          Creates a governed Object Set on <strong>{objectType}</strong>
          {submitted && saveProperty ? (
            <>
              {" "}
              with <span className="hl-mono">{saveProperty} contains “{submitted}”</span>
            </>
          ) : (
            <> matching all instances</>
          )}
          . Free-text search is approximated as a property predicate.
        </p>
        <FormGroup label="Name">
          <InputGroup
            value={setName}
            onChange={(e) => setSetName(e.target.value)}
            placeholder={`${objectType ?? "Type"}Matches`}
            autoFocus
          />
        </FormGroup>
      </RegistryDialog>
    </RegistryPage>
  );
}
