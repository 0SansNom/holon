import { useState } from "react";
import { useWriteTargets } from "../../api/hooks";
import { EmptyState } from "../common/ListPrimitives";
import { OntologyTabHeader } from "../Ontology/OntologyTabLayout";
import { WriteTargetRow } from "./WriteTargetRow";
import { WriteTargetDialog } from "./WriteTargetDialog";

export function WriteTargetsTab() {
  const { data: targets } = useWriteTargets();
  const [creating, setCreating] = useState(false);

  return (
    <div>
      <OntologyTabHeader
        description={
          <>
            Where a declarative Action's <span className="hl-mono">writeback_dataset</span> is allowed to land — the
            table, id column, and the explicit allow-list of properties it may touch. The write itself always goes
            through a governed, approved Action; this only declares what's reachable.
          </>
        }
        createLabel="New write target"
        onCreate={() => setCreating(true)}
      />

      <div className="hl-source-list">
        {targets?.map((t) => (
          <WriteTargetRow key={t.dataset_name} target={t} />
        ))}
        {targets?.length === 0 && (
          <EmptyState actionLabel="New write target" onAction={() => setCreating(true)}>
            No write targets registered yet.
          </EmptyState>
        )}
      </div>

      {creating && <WriteTargetDialog onClose={() => setCreating(false)} />}
    </div>
  );
}
