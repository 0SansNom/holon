import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { EmptyState } from "./ListPrimitives";

describe("EmptyState", () => {
  it("renders children and optional create action", async () => {
    const onAction = vi.fn();
    const user = userEvent.setup();

    render(
      <EmptyState actionLabel="Connect a source" onAction={onAction}>
        No sources connected yet.
      </EmptyState>,
    );

    expect(screen.getByText("No sources connected yet.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Connect a source" }));
    expect(onAction).toHaveBeenCalledOnce();
  });
});
