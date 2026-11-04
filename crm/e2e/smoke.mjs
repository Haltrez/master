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
  await page.click("button:has-text('Table')");
  await page.selectOption("select[aria-label='Stage of Aire Valley Recruitment']", "contacted");
  await page.waitForSelector("text=How did you contact them?");
  await page.click("[role=dialog] button:has-text('Email')");
  await page.waitForTimeout(500);
  await page.reload();
  await page.click("button:has-text('Table')");
  check(await page.locator("select[aria-label='Stage of Aire Valley Recruitment']").inputValue() === "contacted", "agency moved to contacted via the 'how' prompt");

  await page.click("text=Aire Valley Recruitment");
  await page.waitForSelector("[data-testid=log-call]");
  await page.click("[data-testid=log-call]");
  await page.waitForSelector("text=✓ logged");
  check(true, "activity logged from the agency page");
  await page.click("button:has-text('Candidates')");
  await page.waitForSelector("text=Candidate briefs");
  check(true, "candidates tab opens");

  await page.goto(BASE + "/");
  const today = await page.locator("[data-testid=outreach-today]").innerText();
  check(today.trim().startsWith("2"), `Today scorecard counts the 2 actions (shows "${today.trim()}")`);
  await page.screenshot({ path: shots + "today-desktop.png", fullPage: true });

  const phone = await browser.newPage({ ...playwright.devices["iPhone 13"], storageState: await page.context().storageState() });
  for (const p of ["/", "/agencies", "/hot-leads"]) {
    await phone.goto(BASE + p);
    const overflow = await phone.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
    check(overflow <= 1, `no sideways page scroll on a phone: ${p}`);
    await phone.screenshot({ path: shots + `phone${p.replace(/\//g, "_") || "_today"}.png`, fullPage: true });
  }

  await phone.close();
  await page.bringToFront();
  await page.click("header button:has-text('Search')");
  await page.getByLabel("Search", { exact: true }).fill("harl");
  await page.waitForSelector("text=Hot lead · score");
  check(true, "global search (Ctrl/Cmd+K box) finds hot leads");
  console.log("ALL CRM SMOKE CHECKS PASSED");
} finally {
  await browser.close();
}
