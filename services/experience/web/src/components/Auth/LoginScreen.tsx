import { useState } from "react";
import { useNavigate } from "@tanstack/react-router";
import { Button, Card, H4, Radio, RadioGroup } from "@blueprintjs/core";
import { mintToken } from "../../api/identity";
import { clientSecretFor, SEEDED_PRINCIPALS } from "../../api/principals";
import { useAuthStore } from "../../store/auth";

export function LoginScreen() {
  const [selected, setSelected] = useState(SEEDED_PRINCIPALS[0].localName);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const setSession = useAuthStore((s) => s.setSession);
  const navigate = useNavigate();

  const principal = SEEDED_PRINCIPALS.find((p) => p.localName === selected)!;

  async function signIn() {
    setLoading(true);
    setError(null);
    try {
      const token = await mintToken(principal.urn, clientSecretFor(principal.localName));
      setSession({ principalUrn: principal.urn, displayName: principal.displayName, token });
      void navigate({ to: "/objects" });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Sign-in failed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="hl-login-screen">
      <Card className="hl-login-card" elevation={2}>
        <H4>Holon</H4>
        <p style={{ color: "var(--hl-text-muted)", fontSize: 13, marginBottom: 20 }}>
          Sign in as a seeded demo principal — switching between them is the fastest way to see the ReBAC + ABAC
          permission model actually work, not just read about it.
        </p>
        <RadioGroup selectedValue={selected} onChange={(e) => setSelected(e.currentTarget.value)}>
          {SEEDED_PRINCIPALS.map((p) => (
            <Radio key={p.localName} value={p.localName} label={p.displayName}>
              <div style={{ fontSize: 12, color: "var(--hl-text-muted)", marginTop: -6, marginLeft: 24 }}>
                {p.description}
              </div>
            </Radio>
          ))}
        </RadioGroup>
        {error && <p style={{ color: "var(--hl-danger)", fontSize: 12 }}>{error}</p>}
        <Button intent="primary" fill loading={loading} onClick={() => void signIn()} style={{ marginTop: 12 }}>
          Sign in
        </Button>
      </Card>
    </div>
  );
}
