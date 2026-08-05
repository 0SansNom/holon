import { useState } from "react";
import { H3, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useSearch } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";

export function SearchPage() {
  const [query, setQuery] = useState("");
  const [submitted, setSubmitted] = useState("");
  const { data, isLoading } = useSearch(submitted);

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
        onKeyDown={(e) => e.key === "Enter" && setSubmitted(query)}
        rightElement={undefined}
      />
      {isLoading && <Spinner style={{ marginTop: 16 }} />}
      {data && (
        <div style={{ marginTop: 16 }}>
          <Tag minimal style={{ marginBottom: 12 }}>
            {data.total} results
          </Tag>
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
        </div>
      )}
    </div>
  );
}
