import type { CSSProperties, ReactNode } from "react";
import { Button, Dialog, DialogBody, DialogFooter } from "@blueprintjs/core";
import { ErrorCallout } from "./ListPrimitives";

export function RegistryDialog({
  isOpen,
  title,
  onClose,
  error,
  isPending,
  submitLabel,
  onSubmit,
  submitDisabled,
  style,
  children,
}: {
  isOpen: boolean;
  title: string;
  onClose: () => void;
  error: string | null;
  isPending: boolean;
  submitLabel: string;
  onSubmit: () => void;
  submitDisabled?: boolean;
  style?: CSSProperties;
  children: ReactNode;
}) {
  return (
    <Dialog isOpen={isOpen} onClose={onClose} title={title} style={style}>
      <DialogBody>
        {children}
        {error && <ErrorCallout>{error}</ErrorCallout>}
      </DialogBody>
      <DialogFooter
        actions={
          <Button intent="primary" disabled={submitDisabled} loading={isPending} onClick={onSubmit}>
            {submitLabel}
          </Button>
        }
      />
    </Dialog>
  );
}
