import { Button, Menu, MenuDivider, MenuItem, PopoverNext } from "@blueprintjs/core";
import { useNavigate } from "@tanstack/react-router";
import { logout } from "../../api/identity";
import { showError, showSuccess } from "../../lib/toast";
import { useAuthStore } from "../../store/auth";

function initials(name?: string | null): string {
  const trimmed = (name ?? "").trim();
  if (!trimmed) return "?";
  const parts = trimmed.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) {
    return `${parts[0]![0] ?? ""}${parts[1]![0] ?? ""}`.toUpperCase();
  }
  return trimmed.slice(0, 2).toUpperCase() || "?";
}

export function UserMenu() {
  const session = useAuthStore((s) => s.session);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();

  if (!session) return null;

  const urn = session.principal.urn ?? "";
  const displayName = session.principal.display_name?.trim() || urn || "Signed in";

  async function copyUrn() {
    try {
      await navigator.clipboard.writeText(urn);
      showSuccess("URN copied");
    } catch {
      showError("Couldn't copy URN");
    }
  }

  function signOut() {
    void logout().finally(() => {
      clear();
      void navigate({ to: "/login" });
    });
  }

  return (
    <PopoverNext
      placement="bottom-end"
      content={
        <Menu className="hl-user-menu">
          <li className="hl-user-menu-identity" role="none">
            <div className="hl-user-menu-name">{displayName}</div>
            <div className="hl-mono hl-text-muted-sm" title={urn}>
              {urn}
            </div>
          </li>
          <MenuDivider />
          <MenuItem icon="clipboard" text="Copy URN" onClick={() => void copyUrn()} />
          <MenuItem icon="log-out" text="Sign out" intent="danger" onClick={signOut} />
        </Menu>
      }
    >
      <Button
        minimal
        small
        className="hl-user-menu-trigger"
        aria-label={`Signed in as ${displayName}`}
        title={displayName}
      >
        <span className="hl-user-avatar" aria-hidden>
          {initials(displayName)}
        </span>
        <span className="hl-user-menu-label">{displayName}</span>
      </Button>
    </PopoverNext>
  );
}
