import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useNavigate } from "@tanstack/react-router";
import { Button, Icon, Tag, Tooltip } from "@blueprintjs/core";
import { useAuthStore } from "../../store/auth";
import { registerLoginRedirect } from "../../api/authRedirect";
import { useApprovals } from "../../api/hooks";
import { useShellStore } from "../../store/shell";
import { CommandPalette } from "./CommandPalette";
import { ThemeToggle } from "./ThemeToggle";
import { UserMenu } from "./UserMenu";
import { NAV_SECTIONS, SEQUENTIAL_SHORTCUTS } from "./navigation";
import { AppToaster } from "../../lib/toast";

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

function shortcutLabel(): string {
  if (typeof navigator === "undefined") return "Ctrl+K";
  return /Mac|iPhone|iPad/.test(navigator.platform) || /Mac/.test(navigator.userAgent) ? "⌘K" : "Ctrl+K";
}

export function Shell() {
  const session = useAuthStore((s) => s.session);
  const navigate = useNavigate();
  const [paletteOpen, setPaletteOpen] = useState(false);
  const leaderPressedAt = useRef<number | null>(null);
  const { data: pendingApprovals } = useApprovals("pending");
  const pendingCount = pendingApprovals?.length ?? 0;
  const collapsed = useShellStore((s) => s.sidebarCollapsed);
  const toggleSidebar = useShellStore((s) => s.toggleSidebar);

  useEffect(() => {
    if (!session) {
      void navigate({ to: "/login" });
    }
  }, [session, navigate]);

  useEffect(() => {
    registerLoginRedirect(() => {
      void navigate({ to: "/login" });
    });
    return () => registerLoginRedirect(null);
  }, [navigate]);

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        // A Blueprint Dialog's own overlay backdrop can render on top of
        // a later-opened Omnibar, making it visible but unreachable —
        // rather than stack, let Cmd/Ctrl+K only close the palette (never
        // open it) while a create-dialog is already up.
        if (!paletteOpen && document.querySelector(".bp6-dialog")) return;
        setPaletteOpen((open) => !open);
        return;
      }

      if (paletteOpen || isTypingTarget(document.activeElement)) {
        leaderPressedAt.current = null;
        return;
      }

      const key = e.key.toLowerCase();
      if (leaderPressedAt.current !== null && Date.now() - leaderPressedAt.current < 800) {
        leaderPressedAt.current = null;
        const destination = SEQUENTIAL_SHORTCUTS[key];
        if (destination) {
          e.preventDefault();
          void navigate({ to: destination });
        }
        return;
      }

      leaderPressedAt.current = key === "g" ? Date.now() : null;
    }
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [paletteOpen, navigate]);

  if (!session) return null;

  return (
    <div className="hl-shell" data-collapsed={collapsed ? "true" : undefined}>
      <a href="#main-content" className="hl-skip-link">
        Skip to content
      </a>
      <AppToaster />
      <aside className="hl-sidebar" aria-label="Primary">
        <div className="hl-sidebar-brand">
          <Link to="/objects" className="hl-sidebar-brand-link" title="Holon">
            <span className="hl-sidebar-mark" aria-hidden>
              H
            </span>
            <span className="hl-sidebar-brand-text">
              Holon
              <small>Enterprise Knowledge OS</small>
            </span>
          </Link>
        </div>
        <nav className="hl-sidebar-nav" aria-label="Main navigation">
          {NAV_SECTIONS.map((section) => (
            <div key={section.id} className="hl-nav-section">
              <div className="hl-nav-section-label">{section.label}</div>
              {section.items.map((item) => {
                const badge =
                  item.to === "/approvals" && pendingCount > 0 ? (
                    <Tag minimal round intent="primary" className="hl-nav-badge">
                      {pendingCount > 99 ? "99+" : pendingCount}
                    </Tag>
                  ) : null;
                const link = (
                  <Link
                    to={item.to}
                    className="hl-nav-item"
                    activeProps={{ className: "hl-nav-item active" }}
                    activeOptions={{ exact: false }}
                    title={collapsed ? item.label : undefined}
                    aria-label={item.label}
                  >
                    <Icon icon={item.icon} size={14} />
                    <span className="hl-nav-item-label">{item.label}</span>
                    {badge}
                  </Link>
                );
                return (
                  <div key={item.to} className="hl-nav-item-wrap">
                    {collapsed ? (
                      <Tooltip content={item.label} placement="right" compact>
                        {link}
                      </Tooltip>
                    ) : (
                      link
                    )}
                  </div>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="hl-sidebar-footer">
          <Button
            minimal
            small
            fill={!collapsed}
            className="hl-sidebar-collapse"
            icon={collapsed ? "menu-open" : "menu-closed"}
            title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
            onClick={toggleSidebar}
          >
            {collapsed ? null : "Collapse"}
          </Button>
        </div>
      </aside>
      <div className="hl-main">
        <header className="hl-topbar">
          <button type="button" className="hl-topbar-search" onClick={() => setPaletteOpen(true)} aria-label="Search or jump to">
            <Icon icon="search" size={14} />
            <span className="hl-topbar-search-placeholder">Search or jump to…</span>
            <kbd className="hl-kbd">{shortcutLabel()}</kbd>
          </button>
          <div className="hl-topbar-actions">
            <ThemeToggle />
            <UserMenu />
          </div>
        </header>
        <main id="main-content" className="hl-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
      <CommandPalette isOpen={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
