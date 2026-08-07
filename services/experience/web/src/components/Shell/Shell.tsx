import { useEffect } from "react";
import { Link, Outlet, useNavigate, useRouterState } from "@tanstack/react-router";
import { Button, Icon } from "@blueprintjs/core";
import { useAuthStore } from "../../store/auth";

const NAV_ITEMS = [
  { to: "/objects", icon: "cube" as const, label: "Objects" },
  { to: "/sources", icon: "data-connection" as const, label: "Sources" },
  { to: "/ontology", icon: "diagram-tree" as const, label: "Ontology" },
  { to: "/applications", icon: "application" as const, label: "Applications" },
  { to: "/search", icon: "search" as const, label: "Search" },
  { to: "/glossary", icon: "book" as const, label: "Glossary" },
  { to: "/admin", icon: "shield" as const, label: "Admin" },
] as const;

export function Shell() {
  const session = useAuthStore((s) => s.session);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();
  const pathname = useRouterState({ select: (s) => s.location.pathname });

  useEffect(() => {
    if (!session) {
      void navigate({ to: "/login" });
    }
  }, [session, navigate]);

  if (!session) return null;

  return (
    <div className="hl-shell">
      <aside className="hl-sidebar">
        <div className="hl-sidebar-brand">
          Holon
          <small>Enterprise Knowledge OS</small>
        </div>
        <nav style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          {NAV_ITEMS.map((item) => (
            <Link key={item.to} to={item.to} className={`hl-nav-item ${pathname.startsWith(item.to) ? "active" : ""}`}>
              <Icon icon={item.icon} size={14} />
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>
      <div className="hl-main">
        <header className="hl-topbar">
          <div className="hl-mono" style={{ color: "var(--hl-text-muted)", fontSize: 12 }}>
            {session.principalUrn}
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 12 }}>
            <span style={{ fontSize: 13 }}>{session.displayName}</span>
            <Button
              minimal
              small
              icon="log-out"
              onClick={() => {
                clear();
                void navigate({ to: "/login" });
              }}
            >
              Sign out
            </Button>
          </div>
        </header>
        <main className="hl-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
