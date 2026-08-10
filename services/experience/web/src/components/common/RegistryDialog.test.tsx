import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { RegistryDialog } from "./RegistryDialog";

describe("RegistryDialog", () => {
  it("invokes onSubmit when the primary action is clicked", async () => {
    const onSubmit = vi.fn();
    const user = userEvent.setup();

    render(
      <RegistryDialog
        isOpen
        title="New project"
        onClose={() => {}}
        error={null}
        isPending={false}
        submitLabel="Create"
        onSubmit={onSubmit}
      >
        <input aria-label="Project name" />
      </RegistryDialog>,
    );

    await user.click(screen.getByRole("button", { name: "Create" }));
    expect(onSubmit).toHaveBeenCalledOnce();
  });

  it("disables submit while submitDisabled is true", () => {
    render(
      <RegistryDialog
        isOpen
        title="New project"
        onClose={() => {}}
        error={null}
        isPending={false}
        submitLabel="Create"
        submitDisabled
        onSubmit={() => {}}
      >
        <input aria-label="Project name" />
      </RegistryDialog>,
    );

    expect(screen.getByRole("button", { name: "Create" })).toBeDisabled();
  });

  it("surfaces validation errors from the parent", () => {
    render(
      <RegistryDialog
        isOpen
        title="New project"
        onClose={() => {}}
        error="Name already taken"
        isPending={false}
        submitLabel="Create"
        onSubmit={() => {}}
      >
        <input aria-label="Project name" />
      </RegistryDialog>,
    );

    expect(screen.getByText("Name already taken")).toBeInTheDocument();
  });
});
