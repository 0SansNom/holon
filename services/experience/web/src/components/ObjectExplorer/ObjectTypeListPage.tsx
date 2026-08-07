import { Link } from "@tanstack/react-router";
import { Card, H3, Spinner } from "@blueprintjs/core";
import { useObjectTypes } from "../../api/hooks";
import { ClassificationBadge } from "../common/ClassificationBadge";

export function ObjectTypeListPage() {
  const { data, isLoading, error } = useObjectTypes();

  if (isLoading) return <Spinner />;
  if (error) return <p style={{ color: "var(--hl-danger)" }}>{(error as Error).message}</p>;

  return (
    <div>
      <H3>Ontology</H3>
      <p style={{ color: "var(--hl-text-muted)", marginBottom: 20 }}>
        Every ObjectType this ontology defines — instances are only ever reached through it, never a raw table or a
        document dump.
      </p>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(260px, 1fr))", gap: 12 }}>
        {data?.map((ot) => (
          <Link key={ot.urn} to="/objects/$type" params={{ type: ot.name }} style={{ textDecoration: "none" }}>
            <Card interactive style={{ height: "100%", minWidth: 0 }}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "start", gap: 8, marginBottom: 8 }}>
                <strong
                  style={{
                    color: "var(--hl-text)",
                    minWidth: 0,
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                  title={ot.name}
                >
                  {ot.name}
                </strong>
                <div style={{ flexShrink: 0 }}>
                  <ClassificationBadge classification={ot.classification} />
                </div>
              </div>
              <p style={{ fontSize: 12, color: "var(--hl-text-muted)", margin: 0, overflowWrap: "break-word" }}>
                {ot.description}
              </p>
              <div className="hl-mono" style={{ fontSize: 11, color: "var(--hl-text-muted)", marginTop: 10 }}>
                v{ot.version} · {Object.keys(ot.property_mapping).length} properties
              </div>
            </Card>
          </Link>
        ))}
      </div>
    </div>
  );
}
