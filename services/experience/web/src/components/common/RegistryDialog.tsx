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
  intent = "primary",
  style,
  footerStart,
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
  intent?: "primary" | "danger" | "success" | "warning" | "none";
  style?: CSSProperties;
  /** Optional left-side footer controls (e.g. wizard Back). */
  footerStart?: ReactNode;
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
          <>
            {footerStart}
            <Button intent={intent} disabled={submitDisabled} loading={isPending} onClick={onSubmit}>
              {submitLabel}
            </Button>
          </>
        }
      />
    </Dialog>
  );
}
