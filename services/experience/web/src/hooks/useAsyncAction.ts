import { useActionState } from "react";
import { getErrorMessage } from "../api/client";
import { showSuccess } from "../lib/toast";

type ActionState = { error: string | null };

/** React 19 async actions — pending + error without manual try/catch state. */
export function useAsyncAction<TArg>(
  fn: (arg: TArg) => Promise<void>,
  options?: { onSuccess?: () => void; successMessage?: string },
) {
  const [state, dispatch, isPending] = useActionState(
    async (_prev: ActionState, arg: TArg): Promise<ActionState> => {
      try {
        await fn(arg);
        if (options?.successMessage) showSuccess(options.successMessage);
        options?.onSuccess?.();
        return { error: null };
      } catch (err) {
        return { error: getErrorMessage(err) };
      }
    },
    { error: null },
  );

  return { submit: dispatch, error: state.error, isPending };
}
