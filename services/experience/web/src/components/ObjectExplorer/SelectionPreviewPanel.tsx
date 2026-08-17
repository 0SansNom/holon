import { Button, Callout, Tag } from "@blueprintjs/core";
import { Link } from "@tanstack/react-router";
import type {
  ActionDefinition,
  ConditionalFormatRule,
  ObjectType,
  PropertyFormatRule,
  SharedPropertyType,
} from "../../api/knowledge";
import { ObjectActionsBar } from "./ObjectActionsBar";
import { ObjectPropertiesTable } from "./ObjectPropertiesTable";
import { titleOf } from "./objectExplorerUtils";

const PREVIEW_SELECTION_CAP = 20;

/** Foundry-style selection preview — properties + actions without leaving the table. */
export function SelectionPreviewPanel({
  objectTypeName,
  objectType,
  focused,
  selectedRows,
  onFocus,
  onClose,
  onSelectAction,
  formatsBySourceKey,
  conditionalFormatsBySourceKey,
  principalsByUrn,
  sharedPropertyTypes,
  fkFieldTargets,
  relevantActions,
  actionResult,
}: {
  objectTypeName: string;
  objectType?: ObjectType | null;
  focused: Record<string, unknown> | null;
  selectedRows: Record<string, unknown>[];
  onFocus: (id: string) => void;
  onClose: () => void;
  onSelectAction: (actionName: string) => void;
  formatsBySourceKey: Map<string, PropertyFormatRule>;
  conditionalFormatsBySourceKey: Map<string, ConditionalFormatRule[]>;
  principalsByUrn: Map<string, string>;
  sharedPropertyTypes: SharedPropertyType[];
  fkFieldTargets: Map<string, string>;
  relevantActions: ActionDefinition[];
  actionResult: { ok: boolean; message: string } | null;
}) {
  const focusedId = focused?.id != null ? String(focused.id) : null;
  const previewList = selectedRows.slice(0, PREVIEW_SELECTION_CAP);
  const displayTitle = titleOf(focused ?? undefined, objectType);
  const maskedFields = (focused?._maskedFields as string[] | undefined) ?? [];
  const bulk = selectedRows.length > 1;

  return (
    <div className="hl-panel hl-oe-preview">
      <div className="hl-flex-between hl-items-center hl-mb-sm">
        <div className="hl-section-title" style={{ margin: 0 }}>
          Selection preview
        </div>
        <Button minimal small icon="cross" aria-label="Close preview" onClick={onClose} />
      </div>

      {selectedRows.length > 1 && (
        <div className="hl-tag-row hl-mb-sm">
          <Tag minimal intent="primary" icon="selection">
            {selectedRows.length} selected
            {selectedRows.length > PREVIEW_SELECTION_CAP ? ` (showing ${PREVIEW_SELECTION_CAP})` : ""}
          </Tag>
          {previewList.map((row) => {
            const id = String(row.id);
            return (
              <Tag
                key={id}
                minimal
                interactive
                intent={id === focusedId ? "primary" : "none"}
                onClick={() => onFocus(id)}
              >
                {titleOf(row, objectType) || id}
              </Tag>
            );
          })}
        </div>
      )}

      {actionResult && (
        <Callout intent={actionResult.ok ? "success" : "danger"} className="hl-mb-sm">
          {actionResult.message}
        </Callout>
      )}

      {bulk && (
        <ObjectActionsBar
          actions={relevantActions}
          onSelect={onSelectAction}
          title={`Bulk actions (${selectedRows.length})`}
          subtitle="Same parameters applied to every selected object (max 50)."
        />
      )}

      {!focused && <p className="hl-text-muted">Select a row to preview it here.</p>}

      {focused && focusedId && (
        <>
          <div className="hl-flex-between hl-items-start hl-mb-sm">
            <div>
              <div className="hl-body-text" style={{ fontWeight: 600 }}>
                {displayTitle}
              </div>
              <div className="hl-mono hl-text-muted-sm">
                {objectTypeName}/{focusedId}
              </div>
            </div>
            <Link
              to="/objects/$type/$id"
              params={{ type: objectTypeName, id: focusedId }}
              className="hl-link-accent"
            >
              Open Object View
            </Link>
          </div>

          {!bulk && <ObjectActionsBar actions={relevantActions} onSelect={onSelectAction} />}

          <ObjectPropertiesTable
            object={focused}
            objectType={objectType}
            maskedFields={maskedFields}
            fkFieldTargets={fkFieldTargets}
            formatsBySourceKey={formatsBySourceKey}
            conditionalFormatsBySourceKey={conditionalFormatsBySourceKey}
            principalsByUrn={principalsByUrn}
            sharedPropertyTypes={sharedPropertyTypes}
          />
        </>
      )}
    </div>
  );
}
