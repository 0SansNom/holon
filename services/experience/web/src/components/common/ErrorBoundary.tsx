import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Callout, H4 } from "@blueprintjs/core";
import { isStaleChunkError, reloadOnceForStaleChunk } from "../../lib/staleChunk";

interface Props {
  children: ReactNode;
  onReset?: () => void;
}

interface State {
  error: Error | null;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null };

  static getDerivedStateFromError(error: Error): State {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("[UI error]", error, info.componentStack);
    reloadOnceForStaleChunk(error);
  }

  render() {
    if (this.state.error) {
      const stale = isStaleChunkError(this.state.error);
      return (
        <div className="hl-error-boundary">
          <H4>Something went wrong</H4>
          <Callout intent="danger">
            {stale
              ? "A newer version of the app was deployed. Reload to pick it up (or hard-refresh with Cmd+Shift+R / Ctrl+Shift+R)."
              : this.state.error.message}
          </Callout>
          <Button
            className="hl-mt-md"
            onClick={() => {
              if (stale) {
                window.location.reload();
                return;
              }
              this.props.onReset?.();
              this.setState({ error: null });
            }}
          >
            {stale ? "Reload" : "Try again"}
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
