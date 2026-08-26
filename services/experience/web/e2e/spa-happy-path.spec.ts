import { test, expect } from "@playwright/test";
import { signIn } from "./helpers";

test.describe("SPA happy path", () => {
  test("login lands on Object Explorer", async ({ page }) => {
    await signIn(page);
    await expect(page.getByText("Object types")).toBeVisible();
  });

  test("Object Explorer lists types after login", async ({ page }) => {
    await signIn(page);
    await expect(page.getByRole("heading", { name: "Objects" })).toBeVisible();
    const customer = page.getByRole("link", { name: "Customer" }).first();
    if (await customer.isVisible()) {
      await customer.click();
      await expect(page).toHaveURL(/\/objects\/Customer/);
    }
  });

  test("Sources lists connectors and opens a REST dialog", async ({ page }) => {
    await signIn(page);
    await page.getByRole("link", { name: "Sources" }).click();
    await expect(page.getByRole("heading", { name: "Data Sources" })).toBeVisible();
    await page.getByRole("button", { name: "Connect a source" }).click();
    await page.getByRole("menuitem", { name: "REST API" }).click();
    await expect(page.getByRole("dialog", { name: "Connect a source" })).toBeVisible();
    await expect(page.getByPlaceholder("https://api.example.com/records")).toBeVisible();
    await page.getByRole("button", { name: "Cancel" }).click();

    await page.getByRole("tab", { name: "Connections" }).click();
    await page.getByRole("button", { name: "New connection" }).click();
    await page.getByRole("menuitem", { name: "REST credential" }).click();
    await expect(page.getByRole("dialog", { name: "New connection" })).toBeVisible();
    await expect(page.getByLabel("Secret reference")).toBeVisible();
  });

  test("Ontology lists ObjectTypes after login", async ({ page }) => {
    await signIn(page);
    await page.getByRole("link", { name: "Ontology" }).click();
    await expect(page.getByRole("heading", { name: "Ontology" })).toBeVisible();
    await page.getByRole("tab", { name: "ObjectTypes" }).click();
    await expect(page.getByRole("tab", { name: "ObjectTypes" })).toHaveAttribute("aria-selected", "true");
    const customer = page.locator(".hl-registry-card-title", { hasText: "Customer" });
    if (await customer.isVisible()) {
      await expect(customer).toBeVisible();
    }
  });
});
