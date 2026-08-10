import type { ReactNode } from "react";
import { Button, Callout } from "@blueprintjs/core";
import { motion } from "framer-motion";

// The recurring shape of every "browse this ontology concept" tab —
// a responsive card grid, a muted one-line empty state, and a danger
// callout under a create-dialog's fields — previously hand-rolled
// identically in each of Value/Interface/RelationType/... tabs.
//
// The fade+rise-in on mount is this app's one deliberate micro-
// interaction — applied once here so every Ontology/Admin grid that
// already goes through CardGrid picks it up for free.
export function CardGrid({ minWidth = 240, children }: { minWidth?: number; children: ReactNode }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 4 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.15, ease: "easeOut" }}
      style={{ display: "grid", gridTemplateColumns: `repeat(auto-fill, minmax(${minWidth}px, 1fr))`, gap: 10 }}
    >
      {children}
    </motion.div>
  );
}

export function EmptyState({
  children,
  actionLabel,
  onAction,
}: {
  children: ReactNode;
  actionLabel?: string;
  onAction?: () => void;
}) {
  return (
    <div className="hl-empty-state">
      <p className="hl-empty-state-text">{children}</p>
      {actionLabel && onAction && (
        <Button small intent="primary" icon="add" onClick={onAction}>
          {actionLabel}
        </Button>
      )}
    </div>
  );
}

export function ErrorCallout({ children }: { children: ReactNode }) {
  return (
    <Callout intent="danger" className="hl-mt-md">
      {children}
    </Callout>
  );
}
