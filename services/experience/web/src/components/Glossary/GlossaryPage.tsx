import { useMemo, useState } from "react";
import { H3, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useGlossary } from "../../api/hooks";

export function GlossaryPage() {
  const { data, isLoading } = useGlossary();
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return data ?? [];
    return (data ?? []).filter(
      (term) =>
        term.term.toLowerCase().includes(needle) ||
        term.definition.toLowerCase().includes(needle) ||
        term.synonyms.some((s) => s.toLowerCase().includes(needle)),
    );
  }, [data, filter]);

  if (isLoading) return <Spinner />;

  return (
    <div>
      <H3>Business Glossary</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 16 }}>
        Terms resolved (with synonyms) before any semantic fallback — structural resolution first.
      </p>
      <InputGroup
        leftIcon="filter"
        placeholder="Filter terms, definitions, synonyms..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        style={{ marginBottom: 12, maxWidth: 320 }}
      />
      <Tag minimal style={{ marginBottom: 12 }}>
        {filtered.length} of {data?.length ?? 0} terms
      </Tag>
      <div style={{ display: "grid", gap: 8, marginTop: 8 }}>
        {filtered.map((term) => (
          <div key={term.term} className="hl-panel">
            <strong>{term.term}</strong>
            <p style={{ fontSize: 13, margin: "6px 0" }}>{term.definition}</p>
            {term.synonyms.length > 0 && (
              <div style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>Synonyms: {term.synonyms.join(", ")}</div>
            )}
          </div>
        ))}
        {filtered.length === 0 && <p style={{ color: "var(--hl-text-muted)" }}>No terms match "{filter}".</p>}
      </div>
    </div>
  );
}
