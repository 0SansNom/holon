import { type Page, expect } from "@playwright/test";

export const E2E_USER_URN = process.env.HOLON_E2E_USER_URN ?? "hl:acme:global:user:jdoe";
export const E2E_CLIENT_SECRET = process.env.HOLON_E2E_CLIENT_SECRET ?? "jdoe-dev-secret";

export async function signIn(page: Page): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Principal URN").fill(E2E_USER_URN);
  await page.getByLabel("Client secret").fill(E2E_CLIENT_SECRET);
  await page.getByRole("button", { name: "Sign in" }).click();
  await page.waitForURL("**/objects");
  await expect(page.getByRole("heading", { name: "Objects" })).toBeVisible();
}
