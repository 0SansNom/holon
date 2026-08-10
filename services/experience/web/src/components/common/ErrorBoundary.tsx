import { Component, type ErrorInfo, type ReactNode } from "react";
import { Button, Callout, H4 } from "@blueprintjs/core";

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
  }

  render() {
    if (this.state.error) {
      return (
        <div className="hl-error-boundary">
          <H4>Something went wrong</H4>
          <Callout intent="danger">{this.state.error.message}</Callout>
          <Button
            className="hl-mt-md"
            onClick={() => {
              this.props.onReset?.();
              this.setState({ error: null });
            }}
          >
            Try again
          </Button>
        </div>
      );
    }
    return this.props.children;
  }
}
