import { useState } from "react";
import { Button, H3, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";

const PAGE_SIZE = 20;

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
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
    <div>
      <H3>Search</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 16 }}>
        Unified search — entitlement tokens are filtered at the source in OpenSearch itself, never a
        post-filter, so the total you see is exactly what's genuinely visible.
      </p>
      <InputGroup
        large
        leftIcon="search"
        placeholder="Search..."
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && submit(query)}
        rightElement={undefined}
      />

      {isLoading && <Spinner style={{ marginTop: 16 }} />}

      {data && (
        <div style={{ marginTop: 16, display: "flex", gap: 24 }}>
          {facetEntries.length > 0 && (
            <div style={{ width: 180, flexShrink: 0 }}>
              <div
                style={{ fontSize: 11, textTransform: "uppercase", letterSpacing: "0.03em", color: "var(--hl-text-muted)", marginBottom: 8 }}
              >
                Object type
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
                {facetEntries.map(([facet, count]) => (
                  <button
                    key={facet}
                    onClick={() => toggleFacet(facet)}
                    className="hl-facet-item"
                    style={{
                      display: "flex",
                      justifyContent: "space-between",
                      background: objectType === facet ? "var(--hl-accent-soft)" : "transparent",
                      color: objectType === facet ? "var(--hl-accent)" : "var(--hl-text)",
                      border: "none",
                      borderRadius: 4,
                      padding: "6px 8px",
                      fontSize: 13,
                      cursor: "pointer",
                      textAlign: "left",
                    }}
                  >
                    <span>{facet}</span>
                    <span style={{ color: "var(--hl-text-muted)" }}>{count}</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12 }}>
              <Tag minimal>{data.total} results</Tag>
              {objectType && (
                <Tag minimal intent="primary" onRemove={() => toggleFacet(objectType)}>
                  {objectType}
                </Tag>
              )}
            </div>

            {data.results.map((r) => (
              <div key={r.urn} className="hl-panel" style={{ marginBottom: 8 }}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <span className="hl-mono" style={{ fontSize: 12 }}>
                    {r.urn}
                  </span>
                  <ClassificationBadge classification={r.classification} />
                </div>
                <p style={{ fontSize: 13, margin: "8px 0 0" }}>{r.text}</p>
              </div>
            ))}
            {data.results.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No results.</p>}

            {totalPages > 1 && (
              <div style={{ display: "flex", alignItems: "center", gap: 12, marginTop: 12 }}>
                <Button minimal small icon="chevron-left" disabled={page === 0} onClick={() => setPage((p) => p - 1)} />
                <span style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>
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
    </div>
  );
}
