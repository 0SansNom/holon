import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useAuthStore } from "../../store/auth";
import { useShellStore } from "../../store/shell";

vi.mock("../../api/hooks", () => ({
  useApprovals: () => ({ data: [{ id: 1 }, { id: 2 }] }),
}));

vi.mock("./CommandPalette", () => ({
  CommandPalette: () => null,
}));

vi.mock("./ThemeToggle", () => ({
  ThemeToggle: () => <button type="button" aria-label="Theme" />,
}));

vi.mock("../../api/authRedirect", () => ({
  registerLoginRedirect: () => {},
}));

vi.mock("../../lib/toast", () => ({
  AppToaster: () => null,
  showSuccess: vi.fn(),
  showError: vi.fn(),
}));

vi.mock("@tanstack/react-router", () => ({
  Link: ({
    children,
    to,
    className,
    title,
    "aria-label": ariaLabel,
  }: {
    children: import("react").ReactNode;
    to: string;
    className?: string;
    title?: string;
    "aria-label"?: string;
  }) => (
    <a href={to} className={className} title={title} aria-label={ariaLabel}>
      {children}
    </a>
  ),
  Outlet: () => <div>page</div>,
  useNavigate: () => vi.fn(),
}));

import { Shell } from "./Shell";

describe("Shell", () => {
  beforeEach(() => {
    useAuthStore.setState({
      session: {
        principal: {
          urn: "hl:acme:global:user:jdoe",
          type: "user",
          tenant_id: "acme",
          display_name: "Jane Doe",
          on_behalf_of: null,
          country: "FR",
        },
      },
    });
    useShellStore.setState({ sidebarCollapsed: false });
  });

  it("groups navigation and exposes search plus a user menu", () => {
    render(<Shell />);

    expect(screen.getByText("Explore")).toBeInTheDocument();
    expect(screen.getByText("Build")).toBeInTheDocument();
    expect(screen.getByText("Govern")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Objects" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Admin" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /search or jump to/i })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /signed in as jane doe/i })).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
  });

  it("collapses the sidebar to icon-only navigation", async () => {
    const user = userEvent.setup();
    render(<Shell />);

    await user.click(screen.getByRole("button", { name: "Collapse sidebar" }));
    expect(useShellStore.getState().sidebarCollapsed).toBe(true);
    expect(screen.getByRole("button", { name: "Expand sidebar" })).toBeInTheDocument();
  });
});
