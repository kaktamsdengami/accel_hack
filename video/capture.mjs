// Captures the live EXPOSURE site for the demo video: full-page screenshots + boxes of key blocks.
// Usage: node capture.mjs  -> public/shots/*.png + public/shots/shots.json
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = "https://pixel-perfect-recreation-849.lovable.app";
const DASH = `${BASE}/dashboard?match=m-ucl-demo`;
const OUT = "public/shots";
fs.mkdirSync(OUT, { recursive: true });

const browser = await chromium.launch({ channel: process.env.PW_CHANNEL ?? "chrome" });
const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 1.5, colorScheme: "dark" });
const meta = {};

// Box of the smallest ancestor of the element whose text matches `text`, at least minH tall.
async function box(text, minH = 120, exact = false) {
  return page.evaluate(({ text, minH, exact }) => {
    const all = [...document.querySelectorAll("body *")].filter((e) => {
      const t = (e.innerText || "").trim();
      return exact ? t === text : t.toLowerCase().startsWith(text.toLowerCase()) && e.children.length < 4;
    });
    let el = all[0];
    if (!el) return null;
    while (el.parentElement && el.getBoundingClientRect().height < minH) el = el.parentElement;
    const r = el.getBoundingClientRect();
    return { x: r.x + scrollX, y: r.y + scrollY, w: r.width, h: r.height };
  }, { text, minH, exact });
}

async function shot(name, boxes) {
  await page.evaluate(() => document.querySelectorAll('a[href*="lovable.dev"]').forEach((a) => a.closest("div,aside")?.remove()));
  const b = {};
  for (const [k, [text, minH, exact]] of Object.entries(boxes)) b[k] = await box(text, minH, exact);
  const size = await page.evaluate(() => ({ w: document.documentElement.scrollWidth, h: document.documentElement.scrollHeight }));
  await page.screenshot({ path: `${OUT}/${name}.png`, fullPage: true });
  meta[name] = { file: `shots/${name}.png`, ...size, boxes: b };
  console.log(name, size, Object.fromEntries(Object.entries(b).map(([k, v]) => [k, v && Math.round(v.y)])));
}

// Landing
await page.goto(BASE, { waitUntil: "networkidle" });
await page.waitForTimeout(2500);
await shot("landing", {
  hero: ["Football broadcasts. Measured.", 400],
  problem: ["01 / The problem", 250],
  how: ["02 / How it works", 300],
  aiven: ["03 / Built on Aiven", 250],
});

// Dashboard, Live match tab
await page.goto(DASH, { waitUntil: "networkidle" });
await page.waitForTimeout(6000);
await shot("dash", {
  header: ["Broadcast intelligence", 100],
  agents: ["Agents at work", 400],
  vision: ["01 / Perception", 150],
  budget: ["02 / Control", 150],
  analyst: ["03 / Analysis", 150],
  datalayer: ["Data layer", 40],
  kpis: ["Total sponsor seconds", 100],
  evidence: ["Broadcast evidence", 300],
  coverage: ["Coverage", 30],
  rotation: ["LED rotation", 150],
  surfaces: ["Surface breakdown", 200],
  feed: ["Decision feed", 300],
  leaderboard: ["Sponsor leaderboard", 300],
});

// Agents tab
await page.getByRole("button", { name: "Agents", exact: true }).click();
await page.waitForTimeout(3000);
await shot("agents", {
  log: ["Decision log", 300],
  share: ["Claude-call share", 150],
  cost: ["Cost meter", 150],
});

// Ask tab: before and after a live answer
await page.getByRole("button", { name: "Ask", exact: true }).click();
await page.waitForTimeout(2000);
await shot("ask0", { ask: ["Ask the measurements.", 300] });
await page.getByRole("button", { name: "Which sponsor got the most screen time and where?" }).click();
await page.getByText(/had the most measured screen time/).waitFor({ timeout: 120000 });
await page.waitForTimeout(1500);
await shot("ask1", { ask: ["Ask the measurements.", 300], answer: ["Exposure analyst", 250] });

// Sponsor report
await page.goto(DASH, { waitUntil: "networkidle" });
await page.waitForTimeout(5000);
await page.getByText("FedEx", { exact: true }).last().click();
await page.waitForTimeout(1500);
const report = page.getByText("View sponsor report");
if (await report.count()) {
  await report.first().click();
  await page.waitForTimeout(5000);
  console.log("report url", page.url());
  await shot("report", { top: ["Exposure", 300] });
}

fs.writeFileSync(`${OUT}/shots.json`, JSON.stringify(meta, null, 2));
await browser.close();
