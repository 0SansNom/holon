import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Card, FormGroup, H3, InputGroup } from "@blueprintjs/core";
import { TENANT_ID } from "../../api/config";
import { identityApi, login } from "../../api/identity";
import { useAuthStore } from "../../store/auth";

export function LoginScreen() {
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [ssoAvailable, setSsoAvailable] = useState(false);
  const [manualUrn, setManualUrn] = useState(`hl:${TENANT_ID}:global:user:admin`);
  const [manualSecret, setManualSecret] = useState("");
  const setSession = useAuthStore((s) => s.setSession);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();

  useEffect(() => {
    let cancelled = false;
    void identityApi
      .oidcStart()
      .then((result) => {
        if (!cancelled && typeof result?.authorize_url === "string" && result.authorize_url.length > 0) {
          setSsoAvailable(true);
        }
      })
      .catch(() => {
        if (!cancelled) setSsoAvailable(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function signIn() {
    setLoading(true);
    setError(null);
    try {
      await login(manualUrn.trim(), manualSecret);
      const me = await identityApi.whoami();
      setSession({ principal: me });
      void navigate({ to: "/objects" });
    } catch (err) {
      clear();
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="hl-login-screen">
      <Card className="hl-login-card" elevation={2}>
        <div className="hl-login-brand">
          <span className="hl-sidebar-mark" aria-hidden>
            H
          </span>
          <div>
            <H3 className="hl-heading-reset">Holon</H3>
            <div className="hl-text-muted-sm">Enterprise Knowledge OS</div>
          </div>
        </div>
        <p className="hl-login-desc">
          Sign in to browse objects, sources, and the ontology. Use your principal, or SSO if your workspace has it.
        </p>
        <form
          onSubmit={(e) => {
            e.preventDefault();
            if (manualUrn.trim() && manualSecret) void signIn();
          }}
        >
          <FormGroup label="Principal URN" labelFor="login-urn">
            <InputGroup
              id="login-urn"
              value={manualUrn}
              onChange={(e) => setManualUrn(e.currentTarget.value)}
              placeholder="hl:…:global:user:…"
              autoComplete="username"
            />
          </FormGroup>
          <FormGroup label="Client secret" labelFor="login-secret">
            <InputGroup
              id="login-secret"
              type="password"
              value={manualSecret}
              onChange={(e) => setManualSecret(e.currentTarget.value)}
              autoComplete="current-password"
            />
          </FormGroup>
          {error && <p className="hl-text-danger hl-text-muted-sm">{error}</p>}
          <Button
            type="submit"
            intent="primary"
            fill
            large
            loading={loading}
            disabled={!manualUrn.trim() || !manualSecret}
            className="hl-mt-sm"
          >
            Sign in
          </Button>
        </form>
        {ssoAvailable && (
          <Button
            fill
            className="hl-mt-sm"
            onClick={() => {
              void identityApi.oidcStart().then((r) => {
                window.location.href = r.authorize_url;
              });
            }}
          >
            Sign in with SSO
          </Button>
        )}
      </Card>
    </div>
  );
}
