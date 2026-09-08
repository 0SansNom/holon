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
  const hasSaved = explorations.length > 0 || lists.length > 0;

  return (
    <div className="hl-oe-saved-views">
      <div className="hl-flex-row hl-gap-sm hl-items-center" style={{ flexWrap: "wrap" }}>
        {hasSaved && (
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
            <option value="">Current view</option>
            {explorations.length > 0 && (
              <optgroup label="Saved explorations">
                {explorations.map((e) => (
                  <option key={e.id} value={`e:${e.id}`}>
                    {e.name}
                    {e.objectSet ? ` · ${e.objectSet}` : ""}
                  </option>
                ))}
              </optgroup>
            )}
            {lists.length > 0 && (
              <optgroup label="Saved lists">
                {lists.map((l) => (
                  <option key={l.id} value={`l:${l.id}`}>
                    {l.name} ({l.instanceIds.length})
                  </option>
                ))}
              </optgroup>
            )}
          </HTMLSelect>
        )}
        <Button small minimal icon="floppy-disk" onClick={() => { setName(""); setSaveExplorationOpen(true); }}>
          Save view
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
        {activeExploration && (
          <>
            <Tag minimal intent="primary" icon="filter">
              {activeExploration.name}
            </Tag>
            <Button
              minimal
              small
              icon="trash"
              title="Delete saved view"
              onClick={() => onDeleteExploration(activeExploration.id)}
            />
          </>
        )}
        {activeList && (
          <>
            <Tag minimal intent="warning" icon="properties">
              {activeList.name}
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
            Clear
          </Button>
        )}
      </div>

      <RegistryDialog
        isOpen={saveExplorationOpen}
        title="Save view"
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
        <FormGroup label="Name" helperText="Keeps the current set, filters, and column layout in this browser.">
          <InputGroup
            value={name}
            autoFocus
            placeholder="My view"
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
          helperText={`Static list of ${selectionCount} selected object${selectionCount === 1 ? "" : "s"}.`}
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
