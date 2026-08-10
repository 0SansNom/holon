import { useState } from "react";
import { useSearch as useSearchParams } from "@tanstack/react-router";
import { Button, InputGroup, Tag } from "@blueprintjs/core";
import { useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";
import { SearchResultsSkeleton } from "../common/Skeleton";

const PAGE_SIZE = 20;

export function SearchPage() {
  const { q: prefill } = useSearchParams({ strict: false });
  const [query, setQuery] = useState(prefill ?? "");
  const [submitted, setSubmitted] = useState(prefill ?? "");
  const [objectType, setObjectType] = useState<string | undefined>(undefined);
  const [page, setPage] = useState(0);

  const { data, isLoading } = useSearch(submitted, { objectType, from: page * PAGE_SIZE, size: PAGE_SIZE });

  function submit(next: string) {
    setSubmitted(next);
    setObjectType(undefined);
    setPage(0);
  }

  function toggleFacet(facet: string) {
    setObjectType((current) => (current === facet ? undefined : facet));
    setPage(0);
  }

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
            </div>

            {data.results.map((r) => (
              <div key={r.urn} className="hl-panel hl-mb-sm">
                <div className="hl-flex-between">
                  <span className="hl-mono hl-text-muted-sm">{r.urn}</span>
                  <ClassificationBadge classification={r.classification} />
                </div>
                <p className="hl-body-text hl-mt-sm" style={{ marginBottom: 0 }}>
                  {r.text}
                </p>
              </div>
            ))}
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
    </RegistryPage>
  );
}
