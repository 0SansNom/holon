import { useEffect, useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Card, FormGroup, H4, InputGroup } from "@blueprintjs/core";
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
      .then(() => {
        if (!cancelled) setSsoAvailable(true);
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
        <H4>Holon</H4>
        <p className="hl-login-desc">Sign in with SSO, or with a principal URN and client secret from Identity.</p>
        <FormGroup label="Principal URN" labelFor="login-urn">
          <InputGroup
            id="login-urn"
            value={manualUrn}
            onChange={(e) => setManualUrn(e.currentTarget.value)}
            placeholder="hl:…:global:user:…"
          />
        </FormGroup>
        <FormGroup label="Client secret" labelFor="login-secret">
          <InputGroup
            id="login-secret"
            type="password"
            value={manualSecret}
            onChange={(e) => setManualSecret(e.currentTarget.value)}
          />
        </FormGroup>
        {error && <p className="hl-text-danger hl-text-muted-sm">{error}</p>}
        <Button
          intent="primary"
          fill
          loading={loading}
          disabled={!manualUrn.trim() || !manualSecret}
          onClick={() => void signIn()}
          className="hl-mt-sm"
        >
          Sign in
        </Button>
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
