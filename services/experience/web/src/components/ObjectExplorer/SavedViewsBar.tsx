import { useMemo, useState } from "react";
import { Button, FormGroup, HTMLSelect, InputGroup, Tag } from "@blueprintjs/core";
import { RegistryDialog } from "../common/RegistryDialog";
import type { SavedExploration, SavedObjectList } from "./savedViews";

export function SavedViewsBar({
  explorations,
  lists,
  activeExplorationId,
  activeListId,
  selectionCount,
  onLoadExploration,
  onLoadList,
  onClearView,
  onSaveExploration,
  onSaveList,
  onDeleteExploration,
  onDeleteList,
}: {
  explorations: SavedExploration[];
  lists: SavedObjectList[];
  activeExplorationId?: string;
  activeListId?: string;
  selectionCount: number;
  onLoadExploration: (id: string) => void;
  onLoadList: (id: string) => void;
  onClearView: () => void;
  onSaveExploration: (name: string) => void;
  onSaveList: (name: string) => void;
  onDeleteExploration: (id: string) => void;
  onDeleteList: (id: string) => void;
}) {
  const [saveExplorationOpen, setSaveExplorationOpen] = useState(false);
  const [saveListOpen, setSaveListOpen] = useState(false);
  const [name, setName] = useState("");

  const activeExploration = useMemo(
    () => explorations.find((e) => e.id === activeExplorationId),
    [explorations, activeExplorationId],
  );
  const activeList = useMemo(
    () => lists.find((l) => l.id === activeListId),
    [lists, activeListId],
  );

  const selectValue = activeExplorationId
    ? `e:${activeExplorationId}`
    : activeListId
      ? `l:${activeListId}`
      : "";

  return (
    <div className="hl-oe-saved-views hl-mb-md">
      <div className="hl-flex-between hl-items-center hl-mb-sm">
        <div className="hl-section-title" style={{ margin: 0 }}>
          Saved views
        </div>
        <div className="hl-flex-row hl-gap-sm" style={{ flexWrap: "wrap" }}>
          <Button small minimal icon="floppy-disk" onClick={() => { setName(""); setSaveExplorationOpen(true); }}>
            Save exploration
          </Button>
          <Button
            small
            minimal
            icon="properties"
            disabled={selectionCount === 0}
            title={selectionCount === 0 ? "Select rows first" : `Save ${selectionCount} selected IDs`}
            onClick={() => { setName(""); setSaveListOpen(true); }}
          >
            Save list
          </Button>
        </div>
      </div>

      <div className="hl-flex-row hl-gap-sm hl-items-center" style={{ flexWrap: "wrap" }}>
        <HTMLSelect
          value={selectValue}
          onChange={(e) => {
            const v = e.target.value;
            if (!v) {
              onClearView();
              return;
            }
            if (v.startsWith("e:")) onLoadExploration(v.slice(2));
            else if (v.startsWith("l:")) onLoadList(v.slice(2));
          }}
        >
          <option value="">Current (unsaved)</option>
          {explorations.length > 0 && (
            <optgroup label="Explorations">
              {explorations.map((e) => (
                <option key={e.id} value={`e:${e.id}`}>
                  {e.name}
                  {e.objectSet ? ` · set:${e.objectSet}` : ""}
                </option>
              ))}
            </optgroup>
          )}
          {lists.length > 0 && (
            <optgroup label="Lists">
              {lists.map((l) => (
                <option key={l.id} value={`l:${l.id}`}>
                  {l.name} ({l.instanceIds.length})
                </option>
              ))}
            </optgroup>
          )}
        </HTMLSelect>

        {activeExploration && (
          <>
            <Tag minimal intent="primary" icon="filter">
              Exploration
            </Tag>
            <Button
              minimal
              small
              icon="trash"
              title="Delete exploration"
              onClick={() => onDeleteExploration(activeExploration.id)}
            />
          </>
        )}
        {activeList && (
          <>
            <Tag minimal intent="warning" icon="properties">
              List · {activeList.instanceIds.length} IDs
            </Tag>
            <Button
              minimal
              small
              icon="trash"
              title="Delete list"
              onClick={() => onDeleteList(activeList.id)}
            />
          </>
        )}
        {(activeExploration || activeList) && (
          <Button minimal small icon="cross" onClick={onClearView}>
            Clear view
          </Button>
        )}
      </div>

      <p className="hl-text-muted-sm hl-mt-sm">
        Explorations store filters + column layout (local). Lists store static object IDs. Distinct from Ontology
        Object Sets.
      </p>

      <RegistryDialog
        isOpen={saveExplorationOpen}
        title="Save exploration"
        onClose={() => setSaveExplorationOpen(false)}
        error={null}
        isPending={false}
        submitLabel="Save"
        submitDisabled={!name.trim()}
        onSubmit={() => {
          onSaveExploration(name.trim());
          setSaveExplorationOpen(false);
        }}
      >
        <FormGroup label="Name" helperText="Includes current Object Set (if any), filters, and column layout.">
          <InputGroup
            value={name}
            autoFocus
            placeholder="My exploration"
            onChange={(e) => setName(e.target.value)}
          />
        </FormGroup>
      </RegistryDialog>

      <RegistryDialog
        isOpen={saveListOpen}
        title="Save list"
        onClose={() => setSaveListOpen(false)}
        error={null}
        isPending={false}
        submitLabel="Save"
        submitDisabled={!name.trim() || selectionCount === 0}
        onSubmit={() => {
          onSaveList(name.trim());
          setSaveListOpen(false);
        }}
      >
        <FormGroup
          label="Name"
          helperText={`Static list of ${selectionCount} selected object ID${selectionCount === 1 ? "" : "s"}.`}
        >
          <InputGroup
            value={name}
            autoFocus
            placeholder="My list"
            onChange={(e) => setName(e.target.value)}
          />
        </FormGroup>
      </RegistryDialog>
    </div>
  );
}
