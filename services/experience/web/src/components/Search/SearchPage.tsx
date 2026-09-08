import { useEffect, useMemo, useState, type FormEvent, type ReactNode } from "react";
import { Link, useNavigate, useSearch as useSearchParams } from "@tanstack/react-router";
import { Button, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { useCreateObjectSet, useInterfaces, useObjectTypes, useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { RegistryDialog } from "../common/RegistryDialog";
import { SearchResultsSkeleton } from "../common/Skeleton";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import {
  humanizeApiName,
  objectSetBrowsePath,
  parseSearchHitRef,
  preferredSearchProperty,
  searchHitDisplay,
  snippetAroundQuery,
} from "../ObjectExplorer/objectExplorerUtils";
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
  const showInterfaceFilter = durableInterfaces.length > 0 || !!interfaceName;

  function resetFacets() {
    setObjectType(undefined);
    setInterfaceName(undefined);
    setPropFilters({});
    setStructFilterProp("");
    setStructFilterValue("");
    setPage(0);
  }

  function submit(next: string) {
    const q = next.trim();
    if (q !== submitted) resetFacets();
    else setPage(0);
    setSubmitted(q);
    void navigate({ to: "/search", search: q ? { q } : {}, replace: true });
  }

  useEffect(() => {
    const next = (prefill ?? "").trim();
    setQuery(next);
    if (next === submitted) return;
    setSubmitted(next);
    resetFacets();
    // Sync from the URL / command palette only.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [prefill]);

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
  }, { successMessage: `Set “${setName.trim()}” created` });

  const totalPages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;
  const facetEntries = Object.entries(data?.facets ?? {}).sort((a, b) => b[1] - a[1]);
  const propertyFacetEntries = Object.entries(data?.property_facets ?? {});
  const results = data?.results ?? [];

  function onSubmit(e: FormEvent) {
    e.preventDefault();
    submit(query);
  }

  return (
    <RegistryPage
      title="Search"
      description="Find objects by name, id, or any indexed field."
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
            Save as set
          </Button>
        ) : undefined
      }
    >
      <form className="hl-search-query" onSubmit={onSubmit}>
        <InputGroup
          large
          leftIcon="search"
          placeholder="Search objects…"
          value={query}
          autoFocus
          onChange={(e) => setQuery(e.target.value)}
          className="hl-search-query-input"
          rightElement={
            <Button minimal icon="arrow-right" type="submit" aria-label="Search" disabled={!query.trim() && !submitted} />
          }
        />
        {showInterfaceFilter && (
          <HTMLSelect
            large
            value={interfaceName ?? ""}
            onChange={(e) => {
              setInterfaceName(e.target.value || undefined);
              setPage(0);
            }}
            aria-label="Interface"
          >
            <option value="">Any interface</option>
            {durableInterfaces.map((iface) => (
              <option key={iface.name} value={iface.name}>
                {iface.name}
              </option>
            ))}
            {interfaceName && isEphemeralTestName(interfaceName) ? (
              <option value={interfaceName}>{interfaceName} (test)</option>
            ) : null}
          </HTMLSelect>
        )}
      </form>

      {isLoading && submitted && !data && <SearchResultsSkeleton />}

      {data && submitted && (
        <div className="hl-search-layout">
          {facetEntries.length > 0 && (
            <div className="hl-search-facets">
              <div className="hl-section-title hl-mb-sm">Type</div>
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
                  <div className="hl-section-title hl-mb-sm">{humanizeApiName(prop)}</div>
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
            <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-md" style={{ flexWrap: "wrap" }}>
              <Tag minimal>
                {data.total} result{data.total === 1 ? "" : "s"}
              </Tag>
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
                  {interfaceName}
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
                  onRemove={() => {
                    setPropFilters((current) => {
                      const next = { ...current };
                      delete next[prop];
                      return next;
                    });
                    setPage(0);
                  }}
                >
                  {humanizeApiName(prop)}: {value}
                </Tag>
              ))}
            </div>

            {objectType && structFieldKeys.length > 0 && (
              <div className="hl-flex-row hl-gap-sm hl-items-end hl-mb-md" style={{ flexWrap: "wrap" }}>
                <FormGroup label="Field" style={{ marginBottom: 0, minWidth: 180 }}>
                  <HTMLSelect
                    fill
                    value={structFilterProp}
                    onChange={(e) => setStructFilterProp(e.target.value)}
                  >
                    <option value="">Select field…</option>
                    {structFieldKeys.map((key) => (
                      <option key={key} value={key}>
                        {humanizeApiName(key)}
                      </option>
                    ))}
                  </HTMLSelect>
                </FormGroup>
                <FormGroup label="Equals" style={{ marginBottom: 0, minWidth: 160 }}>
                  <InputGroup
                    value={structFilterValue}
                    onChange={(e) => setStructFilterValue(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && applyStructFilter()}
                    placeholder="Value"
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
              const display = searchHitDisplay(r);
              const ref = parseSearchHitRef(r);
              const body = (
                <SearchHitBody hitText={r.text} query={submitted} classification={r.classification} display={display} />
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
              <EmptyState>No results for “{submitted}”. Try another query or clear filters.</EmptyState>
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
        <div className="hl-mt-md">
          <EmptyState>Type a name, id, or keyword and press Enter.</EmptyState>
        </div>
      )}

      <RegistryDialog
        isOpen={savingSet}
        title="Save search as a set"
        onClose={() => setSavingSet(false)}
        error={saveError}
        isPending={savePending}
        submitLabel="Create & browse"
        submitDisabled={!setName.trim() || !objectType || !saveProperty}
        onSubmit={() => submitSaveSet(undefined)}
      >
        <p className="hl-text-muted-sm hl-mb-md">
          Creates a set of {objectType} objects
          {submitted && saveProperty ? (
            <>
              {" "}
              matching <span className="hl-mono">{humanizeApiName(saveProperty)} contains “{submitted}”</span>
            </>
          ) : (
            <> (all instances)</>
          )}
          .
        </p>
        <FormGroup label="Name">
          <InputGroup
            value={setName}
            onChange={(e) => setSetName(e.target.value)}
            placeholder={`${objectType ?? "Type"} matches`}
            autoFocus
          />
        </FormGroup>
      </RegistryDialog>
    </RegistryPage>
  );
}

function SearchHitBody({
  hitText,
  query,
  classification,
  display,
}: {
  hitText: string;
  query: string;
  classification: string;
  display: { title: string; type: string; id: string };
}) {
  const q = query.trim();
  const titleHasQuery = q.length > 0 && display.title.toLowerCase().includes(q.toLowerCase());
  const snippet = !titleHasQuery ? snippetAroundQuery(hitText, q) : "";

  return (
    <>
      <div className="hl-search-result-meta">
        <span className="hl-search-result-type">
          {display.type}
          {display.id ? ` · ${display.id}` : ""}
        </span>
        <ClassificationBadge classification={classification} />
      </div>
      <div className="hl-search-result-title">{highlightQuery(display.title, q)}</div>
      {snippet ? <p className="hl-search-result-snippet">{highlightQuery(snippet, q)}</p> : null}
    </>
  );
}

function highlightQuery(text: string, query: string): ReactNode {
  if (!query) return text;
  const idx = text.toLowerCase().indexOf(query.toLowerCase());
  if (idx < 0) return text;
  return (
    <>
      {text.slice(0, idx)}
      <mark className="hl-search-mark">{text.slice(idx, idx + query.length)}</mark>
      {text.slice(idx + query.length)}
    </>
  );
}
