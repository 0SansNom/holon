import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Icon, Spinner, Tag } from "@blueprintjs/core";
import { useObjectLinks } from "../../api/hooks";
import type { RelatedLink } from "./objectExplorerUtils";

export function RelatedLinkPanel({ type, id, link }: { type: string; id: string; link: RelatedLink }) {
  const [expanded, setExpanded] = useState(false);
  const { data, isLoading } = useObjectLinks(type, id, link.linkName, expanded);

  return (
    <div className="hl-mb-xs">
      <Button minimal small icon={expanded ? "chevron-down" : "chevron-right"} onClick={() => setExpanded((v) => !v)}>
        {link.label}
        {data && (
          <Tag minimal className="hl-ml-xs">
            {data.items.length}
          </Tag>
        )}
      </Button>
      {expanded && (
        <div className="hl-related-links">
          {isLoading && <Spinner size={16} />}
          {data && data.items.length === 0 && (
            <p className="hl-text-muted-sm hl-my-xs">No related instances.</p>
          )}
          {data?.items.map((item) => {
            const itemId = String(item.id);
            const itemLabel = (item.name as string | undefined) ?? itemId;
            return (
              <div key={itemId} className="hl-related-link-row">
                <Link
                  to="/objects/$type/$id"
                  params={{ type: link.relatedType, id: itemId }}
                  className="hl-mono hl-link-accent-sm"
                >
                  <Icon icon="link" size={11} className="hl-mr-xs" />
                  {itemLabel}
                </Link>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
