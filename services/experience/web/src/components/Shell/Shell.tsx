import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Button, Icon, Tag } from "@blueprintjs/core";
import { motion } from "framer-motion";
import { useAuthStore } from "../../store/auth";
import { logout } from "../../api/identity";
import { registerLoginRedirect } from "../../api/authRedirect";
import { CommandPalette } from "./CommandPalette";
import { ThemeToggle } from "./ThemeToggle";
import { NAV_ITEMS, SEQUENTIAL_SHORTCUTS } from "./navigation";
import { AppToaster } from "../../lib/toast";

function isTypingTarget(el: Element | null): boolean {
  if (!el) return false;
  if (el instanceof HTMLElement && el.isContentEditable) return true;
  return el.tagName === "INPUT" || el.tagName === "TEXTAREA" || el.tagName === "SELECT";
}

export function Shell() {
  const session = useAuthStore((s) => s.session);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });
  const [paletteOpen, setPaletteOpen] = useState(false);
  const leaderPressedAt = useRef<number | null>(null);

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
    <div className="hl-shell">
      <a href="#main-content" className="hl-skip-link">
        Skip to content
      </a>
      <AppToaster />
      <aside className="hl-sidebar" aria-label="Primary">
        <div className="hl-sidebar-brand">
          Holon
          <small>Enterprise Knowledge OS</small>
        </div>
        <nav className="hl-flex-col hl-gap-xs" aria-label="Main navigation">
          {NAV_ITEMS.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              className="hl-nav-item"
              activeProps={{ className: "hl-nav-item active" }}
              activeOptions={{ exact: false }}
            >
              <Icon icon={item.icon} size={14} />
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="hl-main">
        <header className="hl-topbar">
          <div className="hl-mono hl-text-muted">
            {session.principal.urn}
          </div>
          <div className="hl-flex-row hl-items-center hl-gap-md">
            <ThemeToggle />
            <Tag
              minimal
              interactive
              icon="search"
              onClick={() => setPaletteOpen(true)}
              style={{ fontSize: 11, color: "var(--hl-text-muted)" }}
            >
              <span className="hl-mono">⌘K</span>
            </Tag>
            <span style={{ fontSize: 13 }}>{session.principal.display_name}</span>
            <Button
              minimal
              small
              icon="log-out"
              onClick={() => {
                // Best-effort — the cookie is cleared server-side, but the
                // local principal is dropped and we navigate away either
                // way, same as every other 401 already does.
                void logout().finally(() => {
                  clear();
                  void navigate({ to: "/login" });
                });
              }}
            >
              Sign out
            </Button>
          </div>
        </header>
        <motion.main
          id="main-content"
          key={pathname}
          className="hl-content"
          tabIndex={-1}
          initial={{ opacity: 0, y: 4 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.12, ease: "easeOut" }}
        >
          <Outlet />
        </motion.main>
      </div>
      <CommandPalette isOpen={paletteOpen} onClose={() => setPaletteOpen(false)} />
    </div>
  );
}
