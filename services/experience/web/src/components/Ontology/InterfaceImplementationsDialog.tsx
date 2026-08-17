import { useMemo } from "react";
import { Link } from "@tanstack/react-router";
import { Button, Callout, Dialog, DialogBody, DialogFooter, Spinner, Tag } from "@blueprintjs/core";
import { useInterfaceObjects, useInterfaces, useObjectTypes } from "../../api/hooks";
import type { InterfaceType, ObjectType } from "../../api/knowledge";
import { titleOf } from "../ObjectExplorer/objectExplorerUtils";

const OBJECT_PREVIEW_LIMIT = 40;

function instanceId(row: Record<string, unknown>, objectType?: ObjectType): string {
  const pk = objectType?.primary_key ?? "id";
  const value = row[pk] ?? row.id;
  return value == null ? "" : String(value);
}

function implementsInterface(
  ot: ObjectType,
  ifaceName: string,
  byName: Map<string, InterfaceType>,
): boolean {
  for (const declared of ot.implements ?? []) {
    if (declared === ifaceName) return true;
    const stack = [...(byName.get(declared)?.parent_interfaces ?? [])];
    const seen = new Set<string>();
    while (stack.length > 0) {
      const current = stack.pop()!;
      if (seen.has(current)) continue;
      seen.add(current);
      if (current === ifaceName) return true;
      stack.push(...(byName.get(current)?.parent_interfaces ?? []));
    }
  }
  return false;
}

export function InterfaceImplementationsDialog({
  iface,
  onClose,
}: {
  iface: InterfaceType;
  onClose: () => void;
}) {
  const { data: objectTypes } = useObjectTypes();
  const { data: interfaces = [] } = useInterfaces();
  const { data: objects, isLoading, error, isFetching } = useInterfaceObjects(iface.name);

  const interfacesByName = useMemo(
    () => new Map(interfaces.map((row) => [row.name, row])),
    [interfaces],
  );

  const implementers = useMemo(
    () => objectTypes.filter((ot) => implementsInterface(ot, iface.name, interfacesByName)),
    [objectTypes, iface.name, interfacesByName],
  );

  const otByName = useMemo(() => {
    const map = new Map<string, ObjectType>();
    for (const ot of objectTypes) map.set(ot.name, ot);
    return map;
  }, [objectTypes]);

  const preview = (objects ?? []).slice(0, OBJECT_PREVIEW_LIMIT);

  return (
    <Dialog isOpen onClose={onClose} title={`Implementations · ${iface.name}`} style={{ width: 720 }}>
      <DialogBody>
        <p className="hl-text-muted-sm">
          ObjectTypes that implement this interface directly or via a child that extends it, plus a polymorphic
          sample from <code>GET /interfaces/{"{name}"}/objects</code>.
        </p>

        <div className="hl-mt-sm">
          <div className="hl-text-muted-sm">Object types ({implementers.length})</div>
          {implementers.length === 0 ? (
            <p className="hl-card-desc">No published ObjectType implements this interface yet.</p>
          ) : (
            <div className="hl-tag-row hl-mt-xs">
              {implementers.map((ot) => (
                <Link key={ot.name} to="/objects/$type" params={{ type: ot.name }} className="hl-link-reset">
                  <Tag minimal>{ot.name}</Tag>
                </Link>
              ))}
            </div>
          )}
        </div>

        <div className="hl-mt-sm">
          <div className="hl-flex-between">
            <div className="hl-text-muted-sm">
              Objects ({objects?.length ?? 0}
              {(objects?.length ?? 0) > OBJECT_PREVIEW_LIMIT ? `, showing ${OBJECT_PREVIEW_LIMIT}` : ""})
            </div>
            {isFetching && !isLoading && <Spinner size={16} />}
          </div>
          {error && (
            <Callout intent="danger" className="hl-mt-xs">
              {(error as Error).message || "Failed to load interface objects"}
            </Callout>
          )}
          {isLoading && (
            <div className="hl-mt-xs">
              <Spinner size={20} />
            </div>
          )}
          {!isLoading && !error && preview.length === 0 && (
            <p className="hl-card-desc">No readable instances for this interface.</p>
          )}
          {!isLoading && preview.length > 0 && (
            <div className="hl-panel hl-table-scroll hl-mt-xs" style={{ maxHeight: 320 }}>
              <table className="bp5-html-table bp5-html-table-condensed bp5-html-table-striped" style={{ width: "100%" }}>
                <thead>
                  <tr>
                    <th>Object type</th>
                    <th>Instance</th>
                  </tr>
                </thead>
                <tbody>
                  {preview.map((row, index) => {
                    const typeName = String(row._objectType ?? "");
                    const ot = otByName.get(typeName);
                    const id = instanceId(row, ot);
                    const label = titleOf(row, ot) || id || "—";
                    return (
                      <tr key={`${typeName}:${id || index}`}>
                        <td>
                          <Link to="/objects/$type" params={{ type: typeName }} className="hl-link-accent">
                            {typeName}
                          </Link>
                        </td>
                        <td>
                          {id ? (
                            <Link
                              to="/objects/$type/$id"
                              params={{ type: typeName, id }}
                              className="hl-link-accent"
                            >
                              {label}
                            </Link>
                          ) : (
                            label
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </DialogBody>
      <DialogFooter actions={<Button onClick={onClose}>Close</Button>} />
    </Dialog>
  );
}
