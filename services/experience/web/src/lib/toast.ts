import { OverlayToaster, Position, type Toaster } from "@blueprintjs/core";

let toaster: Toaster | null = null;

async function getToaster() {
  if (!toaster) {
    toaster = await OverlayToaster.create({
      position: Position.TOP,
      maxToasts: 4,
    });
  }
  return toaster;
}

export function showSuccess(message: string) {
  void getToaster().then((t) => {
    t.show({ message, intent: "success", icon: "tick-circle", timeout: 4000 });
  });
}

export function showError(message: string) {
  void getToaster().then((t) => {
    t.show({ message, intent: "danger", icon: "error", timeout: 6000 });
  });
}

/** Mount once near the app root so toasts render above dialogs. */
export function AppToaster() {
  void getToaster();
  return null;
}
