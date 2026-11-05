// CRM smoke test: log in, create a search, run it, see hot leads and agencies,
// move an agency, log an activity, check the Today scorecard, phone layout.
import { createRequire } from "node:module";
import { mkdirSync } from "node:fs";
const require = createRequire(import.meta.url);
let playwright;
try { playwright = require("playwright"); } catch { playwright = require("/opt/node-tools/node_modules/playwright"); }

const BASE = process.env.CRM_URL || "http://localhost:3000";
const PASSWORD = process.env.CRM_PASSWORD;
const shots = new URL("./screens/", import.meta.url).pathname;
mkdirSync(shots, { recursive: true });

function check(cond, msg) { if (!cond) throw new Error("FAILED: " + msg); console.log("ok -", msg); }

const browser = await playwright.chromium.launch();
try {
  const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });

  await page.goto(BASE + "/hot-leads");
  check(page.url().includes("/login"), "logged-out visit redirects to login");
  const api = await page.request.get(BASE + "/api/engine/hot-leads");
  check(api.status() === 401, "engine proxy refuses requests without a session");

  await page.fill("#password", "wrong");
  await page.click("button:has-text('Sign in')");
  await page.waitForSelector("text=Wrong password");
  check(true, "wrong password rejected");
  await page.fill("#password", PASSWORD);
  await page.click("button:has-text('Sign in')");
  await page.waitForURL(/\/hot-leads/);
  check(true, "login works and returns to the page asked for");

  await page.goto(BASE + "/settings");
  const reposts = page.getByRole("checkbox", { name: /Reposts only/ });
  check(await reposts.isChecked(), "Reposts only is on by default");
  // No Companies House key in the test stack, so nothing can be verified: let unverified companies through.
  await page.getByRole("checkbox", { name: /Real companies only/ }).uncheck();
  await page.click("button:has-text('Save')");
  await page.waitForSelector("text=Saved ✓");

  await page.goto(BASE + "/searches");
  await page.fill("#label", "Accountancy - Leeds");
  const kw = page.getByLabel("Add keyword");
  await kw.fill("management accountant"); await kw.press("Enter");
  await page.fill("#loc", "Leeds");
  await page.click("button:has-text('Save')");
  await page.waitForSelector("text=Accountancy - Leeds");
  check(true, "search created");
  await page.click("[data-testid^=run-]");
  await page.waitForSelector("text=/done: \\d+ ads/", { timeout: 60000 });
  check(true, "run from the UI completes with live status");

  await page.goto(BASE + "/hot-leads");
  await page.waitForSelector("text=Harlow Group");
  check(await page.locator("tbody tr").count() >= 1, "hot leads appear (Harlow Group)");
  check(!(await page.locator("text=Aire Valley Recruitment").count()), "agency ads are not hot leads");

  await page.goto(BASE + "/agencies");
  await page.waitForSelector("text=Aire Valley Recruitment");
  check(true, "agencies appear");
  check(await page.locator("text=Agencies to sell to").count() === 1, "agencies to sell to are listed");
  await page.click("text=Aire Valley Recruitment");
  await page.waitForSelector("[data-testid=log-call]");
  await page.click("[data-testid=log-call]");
  await page.waitForSelector("text=✓ logged");
  check(true, "activity logged from the agency page");
  await page.click("button:has-text('Candidates')");
  await page.waitForSelector("text=Candidate briefs");
  check(true, "candidates tab opens");

  // The simple flow: a lead order from the home page.
  await page.goto(BASE + "/");
  check(await page.locator("text=Add ANTHROPIC_API_KEY").count() === 1, "assistant explains it needs a key");
  await page.click("button:has-text('+ New order')").catch(() => {});
  await page.fill("#agency_name", "Leeds Finance Recruitment");
  await page.selectOption("#industry", "Accountancy & Finance");
  await page.fill("#location", "Leeds");
  await page.fill("#leads_wanted", "3");
  await page.click("button:has-text('Create order & find leads')");
  await page.waitForSelector("[data-testid=order] >> text=Leeds Finance Recruitment");
  await page.waitForSelector("[data-testid=order] >> text=topping up", { timeout: 60000 });
  const progress = await page.locator("[data-testid=order-progress]").first().innerText();
  check(/^[12] \/ 3$/.test(progress.trim()), `order collected and sent its leads (shows "${progress.trim()}")`);
  const csvText = await page.evaluate(() => fetch("/api/engine/orders/1/leads.csv").then((r) => r.text()));
  check(csvText.includes("Harlow Group"), "order CSV has the lead");
  await page.click("[data-testid=order] button:has-text('Pause')");
  await page.waitForSelector("[data-testid=order] >> text=paused");
  check(true, "order paused");
  await page.click("button:has-text('+ New order')");
  await page.fill("#agency_name", "Rival Recruitment");
  await page.selectOption("#industry", "Accountancy & Finance");
  await page.fill("#location", "Leeds");
  await page.click("button:has-text('Create order & find leads')");
  await page.waitForSelector("text=already exclusive to Leeds Finance Recruitment");
  check(true, "a taken industry + region is refused");
  page.once("dialog", (d) => d.accept());
  await page.click("[data-testid=order] button:has-text('Delete')");
  await page.waitForSelector("text=Orders (0)");
  await page.click("button:has-text('Create order & find leads')");
  await page.waitForSelector("[data-testid=order] >> text=Rival Recruitment");
  check(true, "deleting an order frees its industry + region");
  await page.waitForSelector("[data-testid=order] >> text=topping up", { timeout: 60000 });
  await page.goto(BASE + "/leads");
  await page.waitForSelector("text=Harlow Group");
  await page.selectOption("select[aria-label^='Result for Harlow Group']", "meeting");
  await page.waitForTimeout(500);
  await page.reload();
  check(await page.locator("select[aria-label^='Result for Harlow Group']").inputValue() === "meeting", "leads page records what happened with a lead");
  await page.goto(BASE + "/agencies");
  await page.waitForSelector("text=Your clients (1)");
  check(await page.locator("text=1 meetings").count() === 1, "agency shows its orders and results");
  await page.setInputFiles("input[aria-label='CRM list for Rival Recruitment']",
    { name: "crm.csv", mimeType: "text/csv", buffer: Buffer.from("Company Name,Owner\nHarlow Group Ltd,Sarah\nAcme Ltd,Tom\n") });
  await page.waitForSelector("text=2 companies loaded, 2 with an owner");
  check(true, "agency CRM export uploads and is read by its headers");
  check((await page.locator("[data-testid=version]").innerText()).startsWith("Version"), "version is shown");
  await page.goto(BASE + "/");
  await page.selectOption("select[aria-label='Territory industry']", "IT & Tech");
  await page.fill("input[aria-label='Territory town']", "York");
  await page.click("button:has-text('Start collecting')");
  await page.waitForSelector("[data-testid=territory] >> text=IT & Tech - York");
  check(true, "a territory can be collected before it's sold");
  await page.goto(BASE + "/projects");
  await page.fill("textarea[aria-label='Project brief']", "TASK: Build a hot-lead list for a prospect agency, M&E in Manchester.");
  await page.click("button:has-text('Start project')");
  await page.waitForSelector("text=ANTHROPIC_API_KEY");
  check(true, "projects page explains it needs the Claude key");
  await page.goto(BASE + "/");
  await page.screenshot({ path: shots + "orders-desktop.png", fullPage: true });

  const phone = await browser.newPage({ ...playwright.devices["iPhone 13"], storageState: await page.context().storageState() });
  for (const p of ["/", "/projects", "/leads", "/agencies", "/settings"]) {
    await phone.goto(BASE + p);
    const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 1, `no sideways page scroll on a phone: ${p}`);
    await phone.screenshot({ path: shots + `phone${p.replace(/\//g, "_") || "_today"}.png`, fullPage: true });
  }

  await phone.close();
  await page.bringToFront();
  await page.keyboard.press("Control+k");
  await page.getByLabel("Search", { exact: true }).fill("harl");
  await page.waitForSelector("text=Hot lead · score");
  check(true, "global search (Ctrl/Cmd+K box) finds hot leads");
  console.log("ALL CRM SMOKE CHECKS PASSED");
} finally {
  await browser.close();
}
