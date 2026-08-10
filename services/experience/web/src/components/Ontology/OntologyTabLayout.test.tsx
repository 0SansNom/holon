import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { OntologyTabHeader } from "./OntologyTabLayout";

describe("OntologyTabHeader", () => {
  it("fires onCreate from the primary action", async () => {
    const onCreate = vi.fn();
    const user = userEvent.setup();

    render(
      <OntologyTabHeader
        description="Workspace principals with ReBAC grants."
        createLabel="New connection"
        onCreate={onCreate}
      />,
    );

    await user.click(screen.getByRole("button", { name: "New connection" }));
    expect(onCreate).toHaveBeenCalledOnce();
  });

  it("renders trailing controls without a create button", () => {
    render(
      <OntologyTabHeader
        description="Filter object types by group."
        trailing={<select aria-label="Group filter" />}
      />,
    );

    expect(screen.getByLabelText("Group filter")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /new/i })).not.toBeInTheDocument();
  });
});
