import { H3, Spinner } from "@blueprintjs/core";
import { useGlossary } from "../../api/hooks";

export function GlossaryPage() {
  const { data, isLoading } = useGlossary();
  if (isLoading) return <Spinner />;

  return (
    <div>
      <H3>Business Glossary</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 16 }}>
        Terms resolved (with synonyms) before any semantic fallback — structural resolution first.
      </p>
      <div style={{ display: "grid", gap: 8 }}>
        {data?.map((term) => (
          <div key={term.term} className="hl-panel">
            <strong>{term.term}</strong>
            <p style={{ fontSize: 13, margin: "6px 0" }}>{term.definition}</p>
            {term.synonyms.length > 0 && (
              <div style={{ fontSize: 12, color: "var(--hl-text-muted)" }}>Synonyms: {term.synonyms.join(", ")}</div>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
