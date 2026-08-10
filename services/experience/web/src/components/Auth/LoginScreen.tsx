import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Card, H4, Radio, RadioGroup } from "@blueprintjs/core";
import { identityApi, login } from "../../api/identity";
import { clientSecretFor, SEEDED_PRINCIPALS } from "../../api/principals";
import { useAuthStore } from "../../store/auth";

export function LoginScreen() {
  const [selected, setSelected] = useState(SEEDED_PRINCIPALS[0].localName);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setSession = useAuthStore((s) => s.setSession);
  const clear = useAuthStore((s) => s.clear);
  const navigate = useNavigate();

  const principal = SEEDED_PRINCIPALS.find((p) => p.localName === selected)!;

  async function signIn() {
    setLoading(true);
    setError(null);
    try {
      await login(principal.urn, clientSecretFor(principal.localName));
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
        <p className="hl-login-desc">
          Sign in as a seeded demo principal — switching between them is the fastest way to see the ReBAC + ABAC
          permission model actually work, not just read about it.
        </p>
        <RadioGroup selectedValue={selected} onChange={(e) => setSelected(e.currentTarget.value)}>
          {SEEDED_PRINCIPALS.map((p) => (
            <Radio key={p.localName} value={p.localName} label={p.displayName}>
              <div className="hl-radio-desc">{p.description}</div>
            </Radio>
          ))}
        </RadioGroup>
        {error && <p className="hl-text-danger hl-text-muted-sm">{error}</p>}
        <Button intent="primary" fill loading={loading} onClick={() => void signIn()} className="hl-mt-sm">
          Sign in
        </Button>
      </Card>
    </div>
  );
}
