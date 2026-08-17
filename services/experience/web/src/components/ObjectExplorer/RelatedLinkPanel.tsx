import { useState } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Callout, Icon, InputGroup, Spinner, Tag } from "@blueprintjs/core";
import { useDeleteObjectLink, useObjectLinks, usePutObjectLink } from "../../api/hooks";
import { getErrorMessage } from "../../api/client";
import type { RelatedLink } from "./objectExplorerUtils";

/** Foundry-style linked-objects card — counts, expand table, optional link/unlink. */
export function RelatedLinkPanel({
  type,
  id,
  link,
  defaultExpanded,
  writable = false,
}: {
  type: string;
  id: string;
  link: RelatedLink;
  defaultExpanded?: boolean;
  /** When true, show add/remove controls (Object View Links tab). */
  writable?: boolean;
}) {
  const [expanded, setExpanded] = useState(Boolean(defaultExpanded || link.visibility === "prominent"));
  const [targetId, setTargetId] = useState("");
  const [error, setError] = useState<string | null>(null);
  const { data, isLoading, isFetching, refetch } = useObjectLinks(type, id, link.linkName, true);
  const putLink = usePutObjectLink(type, id);
  const deleteLink = useDeleteObjectLink(type, id);
  const count = data?.data.length;
  const midLinks = data?.link_objects ?? [];
  const busy = putLink.isPending || deleteLink.isPending;

  async function onLink() {
    const trimmed = targetId.trim();
    if (!trimmed) return;
    setError(null);
    try {
      const coerced = /^-?\d+$/.test(trimmed) ? Number(trimmed) : trimmed;
      await putLink.mutateAsync({ linkName: link.linkName, targetId: coerced });
      setTargetId("");
      void refetch();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  async function onUnlink(itemId: unknown) {
    setError(null);
    try {
      await deleteLink.mutateAsync({ linkName: link.linkName, targetId: itemId });
      void refetch();
    } catch (err) {
      setError(getErrorMessage(err));
    }
  }

  return (
    <div className={`hl-oe-link-card${link.visibility === "prominent" ? " hl-oe-link-card--prominent" : ""}`}>
      <div className="hl-flex-between hl-items-center">
        <Button
          minimal
          small
          icon={expanded ? "chevron-down" : "chevron-right"}
          onClick={() => setExpanded((v) => !v)}
          className="hl-oe-link-card-toggle"
        >
          <span className="hl-oe-link-card-title">{link.pluralLabel || link.label}</span>
          {link.visibility === "prominent" && (
            <Tag minimal intent="primary" className="hl-ml-xs">
              prominent
            </Tag>
          )}
          {link.cardinality && (
            <Tag minimal className="hl-ml-xs hl-mono">
              {link.cardinality}
            </Tag>
          )}
          {(isLoading || isFetching) && count === undefined ? (
            <Spinner size={12} className="hl-ml-xs" />
          ) : (
            <Tag minimal intent={count && count > 0 ? "success" : "none"} className="hl-ml-xs">
              {count ?? "—"}
            </Tag>
          )}
        </Button>
        <Tag minimal className="hl-mono">
          → {link.relatedType}
        </Tag>
      </div>

      {expanded && (
        <div className="hl-oe-link-card-body">
          {error && (
            <Callout intent="danger" className="hl-mb-sm">
              {error}
            </Callout>
          )}
          {writable && (
            <div className="hl-flex-row hl-gap-sm hl-items-center hl-mb-sm" style={{ flexWrap: "wrap" }}>
              <InputGroup
                small
                placeholder={`${link.relatedType} id`}
                value={targetId}
                onChange={(e) => setTargetId(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") void onLink();
                }}
                className="hl-oe-link-add-input"
              />
              <Button small intent="primary" loading={putLink.isPending} disabled={!targetId.trim()} onClick={() => void onLink()}>
                Link
              </Button>
            </div>
          )}
          {isLoading && <Spinner size={16} />}
          {data && data.data.length === 0 && (
            <p className="hl-text-muted-sm hl-my-xs">No related instances.</p>
          )}
          {data && data.data.length > 0 && (
            <table className="hl-data-table hl-data-table-compact">
              <thead>
                <tr>
                  <th>Title</th>
                  <th>Id</th>
                  {writable && <th />}
                </tr>
              </thead>
              <tbody>
                {data.data.map((item) => {
                  const itemId = String(item.id);
                  const itemLabel =
                    (item.title as string | undefined) ?? (item.name as string | undefined) ?? itemId;
                  return (
                    <tr key={itemId} className="hl-data-table-row">
                      <td>
                        <Link
                          to="/objects/$type/$id"
                          params={{ type: link.relatedType, id: itemId }}
                          className="hl-link-accent"
                        >
                          <Icon icon="link" size={11} className="hl-mr-xs" />
                          {itemLabel}
                        </Link>
                      </td>
                      <td className="hl-mono hl-text-muted-sm">{itemId}</td>
                      {writable && (
                        <td>
                          <Button
                            minimal
                            small
                            icon="unlink"
                            disabled={busy}
                            title="Unlink"
                            onClick={() => void onUnlink(item.id)}
                          />
                        </td>
                      )}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
          {midLinks.length > 0 && (
            <div className="hl-mt-sm">
              <div className="hl-text-muted-sm hl-mb-xs">Link objects (mid)</div>
              <div className="hl-tag-row">
                {midLinks.map((mid, i) => {
                  const midType = mid.object_type;
                  const midId = mid.object?.id != null ? String(mid.object.id) : null;
                  const midTitle =
                    (mid.object?.title as string | undefined) ??
                    (mid.object?.name as string | undefined) ??
                    midId ??
                    `mid-${i}`;
                  if (midType && midId) {
                    return (
                      <Link key={`${midType}-${midId}-${i}`} to="/objects/$type/$id" params={{ type: midType, id: midId }}>
                        <Tag minimal interactive icon="diagram-tree">
                          {midType}/{midTitle}
                        </Tag>
                      </Link>
                    );
                  }
                  return (
                    <Tag key={`mid-${i}`} minimal>
                      {midTitle}
                    </Tag>
                  );
                })}
              </div>
            </div>
          )}
          {data?.direction && (
            <p className="hl-text-muted-sm hl-mt-xs">
              Direction: {data.direction}
              {data.storage_kind ? ` · ${data.storage_kind}` : ""}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
