// One-slide pitch (docs/pitch-slide.pptx). Numbers: eval/runs/20261022T113439Z-ucl-vision-run.json.
// Run: NODE_PATH=<dir with pptxgenjs+jszip> SKILL_DIR=<pptx skill dir> node scripts/pitch_slide.js docs/pitch-slide.pptx
// Then import into Google Slides and share "Anyone with the link can view".
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.SKILL_DIR + "/scripts/apply_theme.js");

const INK = "0D0E0C", PANEL = "161814", LINE = "2E302A", IVORY = "EEE9DE",
      MUTED = "A39F94", DIM = "6F6C64", GOLD = "C9A766";
const HEAD = "Georgia", BODY = "Arial";

const THEME = {
  name: "Exposure",
  headFontFace: HEAD, bodyFontFace: BODY,
  colors: { dk1: INK, lt1: IVORY, dk2: PANEL, lt2: MUTED, accent1: GOLD, accent2: "8C8678",
            accent3: LINE, accent4: DIM, accent5: "B8B2A4", accent6: "5A5850", hlink: GOLD, folHlink: MUTED },
};

(async () => {
  const pres = new pptxgen();
  pres.layout = "LAYOUT_WIDE"; // 13.333 x 7.5
  pres.title = "EXPOSURE — pitch slide";
  pres.theme = { headFontFace: HEAD, bodyFontFace: BODY };
  const s = pres.addSlide();
  s.background = { color: INK };

  const t = (text, o) => s.addText(text, { isTextBox: true, margin: 0, fontFace: BODY, color: IVORY, valign: "top", ...o });
  const line = (x, y, w, h) => s.addShape(pres.shapes.LINE, { x, y, w, h, line: { color: LINE, width: 0.75 } });

  // Top row
  t("EXPOSURE", { x: 0.6, y: 0.42, w: 3, h: 0.3, fontFace: HEAD, fontSize: 14, bold: true, color: GOLD, charSpacing: 5 });
  t("Data-Centric Agents  ·  Accel × Aiven  ·  Paris 2026", { x: 6.8, y: 0.45, w: 5.93, h: 0.3, fontSize: 10, color: DIM, align: "right", charSpacing: 1 });

  // Headline + promise
  t("Sponsors pay millions for screen time.\nNo club can prove how much they got.", {
    x: 0.6, y: 0.85, w: 9.5, h: 1.05, fontFace: HEAD, fontSize: 28, color: IVORY, lineSpacingMultiple: 0.95 });
  t([
    { text: "EXPOSURE", options: { bold: true, color: GOLD } },
    { text: " is a team of AI agents that watches the broadcast and logs every second each sponsor is on screen, per surface, with the frame as proof." },
  ], { x: 0.6, y: 2.0, w: 9.8, h: 0.5, fontSize: 14, color: MUTED });

  line(0.6, 2.68, 12.13, 0);

  // Column geometry
  const top = 2.88, bottom = 6.42;
  const c1 = { x: 0.6, w: 3.85 }, c2 = { x: 4.85, w: 3.95 }, c3 = { x: 9.2, w: 3.53 };
  line(4.65, top, 0, bottom - top);
  line(9.0, top, 0, bottom - top);
  const header = (c, n, label) => t(`${n}   ${label}`, { x: c.x, y: top, w: c.w, h: 0.25, fontSize: 10, bold: true, color: GOLD, charSpacing: 1.5 });

  // 01 Customer
  header(c1, "01", "THE CUSTOMER");
  const blocks = [
    ["Who", "Club commercial managers, starting with 2nd-tier clubs selling LED boards, shirts and backdrops."],
    ["Problem", "At renewal sponsors ask: how many seconds was I on TV, and where? Today: a manual count or a guess."],
    ["Why now", "Vision models now read stylised logos OCR misses, cheaply enough for every club, every match."],
    ["First users", "Pilot done with a 2nd-tier club, a second agreed. Next: renewal season, then leagues for all their clubs."],
  ];
  blocks.forEach(([k, v], i) => {
    const y = top + 0.4 + i * 0.79;
    t(k.toUpperCase(), { x: c1.x, y, w: c1.w, h: 0.2, fontSize: 9.5, bold: true, color: IVORY, charSpacing: 1 });
    t(v, { x: c1.x, y: y + 0.22, w: c1.w, h: 0.55, fontSize: 11.5, color: MUTED, lineSpacingMultiple: 1.0 });
  });

  // 02 Agents
  header(c2, "02", "THREE AGENTS, ONE LEDGER");
  const agents = [
    ["Vision Reader", "Reads every frame, picks only from the club's sponsor list. Not sure? It says unknown."],
    ["Budget Guard", "Prices each model call before making it, under a hard per-match budget."],
    ["Sponsor Analyst", "Answers open questions by calling the data API as tools, citing seconds and frames."],
  ];
  agents.forEach(([name, desc], i) => {
    const y = top + 0.42 + i * 0.74;
    s.addShape(pres.shapes.OVAL, { x: c2.x, y, w: 0.36, h: 0.36, fill: { color: INK }, line: { color: GOLD, width: 1 } });
    t(String(i + 1), { x: c2.x, y: y + 0.03, w: 0.36, h: 0.3, fontFace: HEAD, fontSize: 13, color: GOLD, align: "center", valign: "middle" });
    t(name, { x: c2.x + 0.55, y: y - 0.02, w: c2.w - 0.55, h: 0.24, fontSize: 12.5, bold: true, color: IVORY });
    t(desc, { x: c2.x + 0.55, y: y + 0.23, w: c2.w - 0.55, h: 0.45, fontSize: 10.5, color: MUTED });
  });
  // Data rail
  const ry = top + 2.72;
  t("LIVE DATA LAYER ON AIVEN", { x: c2.x, y: ry, w: c2.w, h: 0.2, fontSize: 9, bold: true, color: DIM, charSpacing: 1 });
  const pills = [["Kafka", 0.72], ["ClickHouse", 0.98], ["Valkey", 0.72], ["Postgres", 0.87]];
  let px = c2.x;
  pills.forEach(([name, w], i) => {
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: px, y: ry + 0.27, w, h: 0.32, rectRadius: 0.16, fill: { color: PANEL }, line: { color: LINE, width: 0.75 } });
    t(name, { x: px, y: ry + 0.27, w, h: 0.32, fontSize: 9.5, color: IVORY, align: "center", valign: "middle" });
    px += w;
    if (i < pills.length - 1) { t("›", { x: px, y: ry + 0.26, w: 0.2, h: 0.32, fontSize: 12, color: GOLD, align: "center", valign: "middle" }); px += 0.2; }
  });
  t("Unmeasured seconds are logged, never counted.", { x: c2.x, y: ry + 0.7, w: c2.w, h: 0.25, fontSize: 9.5, italic: true, color: MUTED });

  // 03 Evidence
  header(c3, "03", "EVIDENCE IT WORKS");
  const stats = [["116", "frames read, 81 logo sightings"], ["0", "failed calls, 0 off-list brands"], ["$0.12", "per match-minute"], ["96%", "pilot vs 18–56% OCR*"]];
  stats.forEach(([n, l], i) => {
    const x = c3.x + (i % 2) * 1.82, y = top + 0.38 + Math.floor(i / 2) * 0.86;
    t(n, { x, y, w: 1.7, h: 0.48, fontFace: HEAD, fontSize: 28, color: GOLD });
    t(l, { x, y: y + 0.48, w: 1.68, h: 0.34, fontSize: 9, color: MUTED });
  });
  const by = top + 2.2;
  t("SCREEN TIME, 58 s UCL SEGMENT", { x: c3.x, y: by, w: c3.w, h: 0.2, fontSize: 9, bold: true, color: DIM, charSpacing: 1 });
  const bars = [["FedEx", 20.0], ["PlayStation", 12.0], ["Heineken", 6.5], ["Mastercard", 1.0]];
  const trackX = c3.x + 1.0, trackW = 2.0;
  bars.forEach(([b, v], i) => {
    const y = by + 0.3 + i * 0.25;
    t(b, { x: c3.x, y, w: 0.95, h: 0.18, fontSize: 9, color: IVORY, valign: "middle" });
    s.addShape(pres.shapes.RECTANGLE, { x: trackX, y: y + 0.05, w: trackW, h: 0.08, fill: { color: PANEL }, line: { type: "none" } });
    s.addShape(pres.shapes.RECTANGLE, { x: trackX, y: y + 0.05, w: Math.max(0.04, trackW * v / 20), h: 0.08, fill: { color: i === 0 ? GOLD : "8C8678" }, line: { type: "none" } });
    t(`${v.toFixed(1)} s`, { x: trackX + trackW + 0.08, y, w: 0.45, h: 0.18, fontSize: 9, color: MUTED, align: "right", valign: "middle" });
  });

  // Footer: difference + honesty
  line(0.6, 6.6, 12.13, 0);
  t([
    { text: "WHY US  ", options: { bold: true, color: GOLD, charSpacing: 1, fontSize: 10 } },
    { text: "Not a count after the match: a live ledger where every second has a frame, a confidence and a cost.", options: { fontFace: HEAD, italic: true, fontSize: 13, color: IVORY } },
  ], { x: 0.6, y: 6.72, w: 9.4, h: 0.3, valign: "middle" });
  t("*Pilot on our earlier prototype, partly circular; independent labelling next. Evidence: recorded broadcast replayed through the live pipeline on Aiven Runtime. Repo: github.com/kaktamsdengami/accel_hack-",
    { x: 0.6, y: 7.06, w: 12.13, h: 0.22, fontSize: 8, color: DIM });
  t("Live dashboard ↗", { x: 10.4, y: 6.72, w: 2.33, h: 0.3, fontSize: 11, bold: true, color: GOLD, align: "right", valign: "middle",
    hyperlink: { url: "https://pixel-perfect-recreation-849.lovable.app/dashboard?match=m-ucl-demo" } });

  const out = process.argv[2];
  await pres.writeFile({ fileName: out });
  await applyTheme(out, THEME);
})();
