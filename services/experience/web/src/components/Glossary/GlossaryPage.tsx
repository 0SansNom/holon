import { useMemo, useState } from "react";
import { InputGroup, Tag } from "@blueprintjs/core";
import { useGlossary } from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { RegistryPage } from "../common/PageLayout";

export function GlossaryPage() {
  const { data } = useGlossary();
  const [filter, setFilter] = useState("");

  const filtered = useMemo(() => {
    const needle = filter.trim().toLowerCase();
    if (!needle) return data;
    return data.filter(
      (term) =>
        term.term.toLowerCase().includes(needle) ||
        term.definition.toLowerCase().includes(needle) ||
        term.synonyms.some((s) => s.toLowerCase().includes(needle)),
    );
  }, [data, filter]);

  return (
    <RegistryPage
      title="Business Glossary"
      description={
        <>
          Terms resolved (with synonyms) before any semantic fallback — structural resolution first, served by the
          Knowledge service.
        </>
      }
    >
      <InputGroup
        leftIcon="filter"
        placeholder="Filter terms, definitions, synonyms..."
        value={filter}
        onChange={(e) => setFilter(e.target.value)}
        className="hl-mb-md hl-filter-input"
      />
      <Tag minimal className="hl-mb-md">
        {filtered.length} of {data.length} terms
      </Tag>
      <div className="hl-grid-gap-sm">
        {filtered.map((term) => (
          <div key={term.term} className="hl-panel">
            <strong>{term.term}</strong>
            <p className="hl-body-text">{term.definition}</p>
            {term.synonyms.length > 0 && (
              <div className="hl-text-muted">Synonyms: {term.synonyms.join(", ")}</div>
            )}
          </div>
        ))}
        {filtered.length === 0 && (
          <EmptyState>{filter ? `No terms match "${filter}".` : "No glossary terms yet."}</EmptyState>
        )}
      </div>
    </RegistryPage>
  );
}
