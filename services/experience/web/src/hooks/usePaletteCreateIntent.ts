import { useEffect } from "react";
import { usePaletteIntentStore, type PaletteIntent } from "../store/paletteIntent";

/** Opens a create dialog when the command palette queues the matching intent. */
export function usePaletteCreateIntent(intent: PaletteIntent, setCreating: (open: boolean) => void) {
  const currentIntent = usePaletteIntentStore((s) => s.intent);
  const consume = usePaletteIntentStore((s) => s.consume);

  useEffect(() => {
    if (currentIntent === intent) {
      setCreating(true);
      consume();
    }
  }, [currentIntent, intent, consume, setCreating]);
}
