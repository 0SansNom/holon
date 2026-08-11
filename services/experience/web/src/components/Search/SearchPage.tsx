import { useMemo, useState } from "react";
import { Link, useNavigate, useSearch as useSearchParams } from "@tanstack/react-router";
import { Button, FormGroup, InputGroup, Tag } from "@blueprintjs/core";
import { useCreateObjectSet, useObjectTypes, useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { RegistryDialog } from "../common/RegistryDialog";
import { SearchResultsSkeleton } from "../common/Skeleton";
import { useAsyncAction } from "../../hooks/useAsyncAction";
import { objectSetBrowsePath, parseSearchHitRef, preferredSearchProperty } from "../ObjectExplorer/objectExplorerUtils";

const PAGE_SIZE = 20;

export function SearchPage() {
  const navigate = useNavigate();
  const { q: prefill } = useSearchParams({ strict: false });
  const [query, setQuery] = useState(prefill ?? "");
  const [submitted, setSubmitted] = useState(prefill ?? "");
  const [objectType, setObjectType] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(0);
  const [savingSet, setSavingSet] = useState(false);
  const [setName, setSetName] = useState("");

  const { data: objectTypes } = useObjectTypes();
  const createObjectSet = useCreateObjectSet();
  const { data, isLoading } = useSearch(submitted, { objectType, from: page * PAGE_SIZE, size: PAGE_SIZE });

  const selectedOt = useMemo(
    () => (objectTypes ?? []).find((ot) => ot.name === objectType),
    [objectTypes, objectType],
  );
  const saveProperty = preferredSearchProperty(selectedOt);

  function submit(next: string) {
    setSubmitted(next);
    setObjectType(undefined);
    setPage(0);
  }

  function toggleFacet(facet: string) {
    setObjectType((current) => (current === facet ? undefined : facet));
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

  return (
    <RegistryPage
      title="Search"
      description={
        <>
          Unified search (Knowledge `/search`) — entitlement tokens are filtered at the source in OpenSearch,
          never a post-filter, so the total you see is exactly what's genuinely visible.
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
      <InputGroup
        large
        leftIcon="search"
        placeholder="Search..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit(query)}
      />

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
            </div>
          )}

          <div className="hl-flex-1 hl-min-w-0">
            <div className="hl-flex-row hl-items-center hl-gap-sm hl-mb-md">
              <Tag minimal>{data.total} results</Tag>
              {objectType && (
                <Tag minimal intent="primary" onRemove={() => toggleFacet(objectType)}>
                  {objectType}
                </Tag>
              )}
              {objectType && !saveProperty && (
                <span className="hl-text-muted-sm">Select a type with properties to save as an Object Set.</span>
              )}
            </div>

            {data.results.map((r) => {
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
            {data.results.length === 0 && (
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
