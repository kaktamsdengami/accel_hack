// One-slide pitch (docs/pitch-slide.pptx), one column per judging criterion.
// Numbers: eval/runs/20261022T113439Z-ucl-vision-run.json.
// Run: NODE_PATH=<dir with pptxgenjs+jszip> SKILL_DIR=<pptx skill dir> node scripts/pitch_slide.js docs/pitch-slide.pptx
// Then import into Google Slides and share "Anyone with the link can view".
const pptxgen = require("pptxgenjs");
const { applyTheme } = require(process.env.SKILL_DIR + "/scripts/apply_theme.js");

const INK = "0D0E0C", PANEL = "161814", LINE = "2E302A", IVORY = "EEE9DE",
      MUTED = "A39F94", DIM = "6F6C64", GOLD = "C9A766", BAR = "8C8678";
const HEAD = "Georgia", BODY = "Arial";

const THEME = {
  name: "Exposure",
  headFontFace: HEAD, bodyFontFace: BODY,
  colors: { dk1: INK, lt1: IVORY, dk2: PANEL, lt2: MUTED, accent1: GOLD, accent2: BAR,
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

  // Headline: what we do
  t("AI agents that measure every second a sponsor is on screen.\nLive, per surface, with the frame as proof.", {
    x: 0.6, y: 0.82, w: 12.13, h: 0.95, fontFace: HEAD, fontSize: 26, color: IVORY, lineSpacingMultiple: 0.95 });
  t([
    { text: "EXPOSURE", options: { bold: true, color: GOLD } },
    { text: " turns a football broadcast into an auditable sponsor ledger that clubs bring to every renewal." },
  ], { x: 0.6, y: 1.85, w: 12.13, h: 0.3, fontSize: 14, color: MUTED });

  line(0.6, 2.38, 12.13, 0);

  // Four columns, one per judging criterion: label, one-line answer, proof
  const top = 2.56, bottom = 6.42, gap = 0.4;
  const widths = [3.05, 3.05, 2.35, 2.48];
  const cols = []; let cx = 0.6;
  widths.forEach((w, i) => { cols.push({ x: cx, w }); cx += w + gap; if (i < 3) line(cx - gap / 2, top, 0, bottom - top); });
  const [c1, c2, c3, c4] = cols;
  const body = top + 0.98;
  const criterion = (c, n, label, answer) => {
    t(`${n}  ${label}`, { x: c.x, y: top, w: c.w, h: 0.22, fontSize: 9, bold: true, color: GOLD, charSpacing: 1.2 });
    t(answer, { x: c.x, y: top + 0.3, w: c.w, h: 0.58, fontFace: HEAD, fontSize: 14, color: IVORY, lineSpacingMultiple: 0.95 });
  };

  // 01 Customer problem & value
  criterion(c1, "01", "CUSTOMER PROBLEM & VALUE", "A buyer with a deadline: the sponsor renewal.");
  const blocks = [
    ["Who", "Club commercial teams selling LED boards, shirts and backdrops; 2nd-tier clubs first."],
    ["Value", "Proof of seconds on screen per sponsor and surface, instead of a manual count."],
    ["Why now", "Vision models read stylised logos OCR misses, at ≈ $0.12 per match-minute."],
    ["First users", "Pilot done with a 2nd-tier club, a second agreed; then leagues, for all their clubs."],
  ];
  blocks.forEach(([k, v], i) => {
    const y = body + i * 0.72;
    t(k.toUpperCase(), { x: c1.x, y, w: c1.w, h: 0.18, fontSize: 8.5, bold: true, color: IVORY, charSpacing: 1 });
    t(v, { x: c1.x, y: y + 0.2, w: c1.w, h: 0.48, fontSize: 10.5, color: MUTED });
  });

  // 02 Agent capability
  criterion(c2, "02", "AGENT CAPABILITY", "Three agents that decide, budget and explain.");
  const agents = [
    ["Vision Reader", "Reads each frame, picks only from the sponsor list. Unsure? Says unknown."],
    ["Budget Guard", "Prices every model call first; stops at a hard per-match budget."],
    ["Sponsor Analyst", "Plans its own tool calls on the data API; cites seconds and frames."],
  ];
  agents.forEach(([name, desc], i) => {
    const y = body + 0.02 + i * 0.95;
    s.addShape(pres.shapes.OVAL, { x: c2.x, y, w: 0.34, h: 0.34, fill: { color: INK }, line: { color: GOLD, width: 1 } });
    t(String(i + 1), { x: c2.x, y: y + 0.02, w: 0.34, h: 0.3, fontFace: HEAD, fontSize: 12, color: GOLD, align: "center", valign: "middle" });
    t(name, { x: c2.x + 0.5, y: y - 0.01, w: c2.w - 0.5, h: 0.24, fontSize: 12, bold: true, color: IVORY });
    t(desc, { x: c2.x + 0.5, y: y + 0.24, w: c2.w - 0.5, h: 0.6, fontSize: 10.5, color: MUTED });
  });

  // 03 Data at the core
  criterion(c3, "03", "DATA AT THE CORE", "No live data, no product.");
  const stack = [
    ["Kafka", "frames + decisions, streamed"],
    ["ClickHouse", "per-second intervals, coverage"],
    ["Valkey", "live counters, atomic budget"],
    ["Postgres + pgvector", "evidence frames, logo memory"],
  ];
  stack.forEach(([name, role], i) => {
    const y = body + i * 0.7;
    s.addShape(pres.shapes.ROUNDED_RECTANGLE, { x: c3.x, y, w: c3.w, h: 0.56, rectRadius: 0.08, fill: { color: PANEL }, line: { color: LINE, width: 0.75 } });
    t(name, { x: c3.x + 0.14, y: y + 0.07, w: c3.w - 0.28, h: 0.2, fontSize: 10.5, bold: true, color: IVORY });
    t(role, { x: c3.x + 0.14, y: y + 0.29, w: c3.w - 0.28, h: 0.2, fontSize: 9, color: MUTED });
  });

  // 04 Evidence it works
  criterion(c4, "04", "EVIDENCE IT WORKS", "Measured on a real broadcast.");
  const stats = [["116", "frames read"], ["0", "errors, off-list brands"], ["$0.12", "per match-minute"], ["96%", "pilot vs 18–56% OCR*"]];
  stats.forEach(([n, l], i) => {
    const x = c4.x + (i % 2) * 1.3, y = body - 0.08 + Math.floor(i / 2) * 0.8;
    t(n, { x, y, w: 1.25, h: 0.44, fontFace: HEAD, fontSize: 24, color: GOLD });
    t(l, { x, y: y + 0.44, w: 1.2, h: 0.3, fontSize: 8.5, color: MUTED });
  });
  const by = body + 1.6;
  t("SCREEN TIME, 58 s UCL SEGMENT", { x: c4.x, y: by, w: c4.w, h: 0.2, fontSize: 8.5, bold: true, color: DIM, charSpacing: 1 });
  const bars = [["FedEx", 20.0], ["PlayStation", 12.0], ["Heineken", 6.5], ["Mastercard", 1.0]];
  const trackX = c4.x + 0.85, trackW = 1.15;
  bars.forEach(([b, v], i) => {
    const y = by + 0.28 + i * 0.24;
    t(b, { x: c4.x, y, w: 0.82, h: 0.18, fontSize: 8.5, color: IVORY, valign: "middle" });
    s.addShape(pres.shapes.RECTANGLE, { x: trackX, y: y + 0.05, w: trackW, h: 0.08, fill: { color: PANEL }, line: { type: "none" } });
    s.addShape(pres.shapes.RECTANGLE, { x: trackX, y: y + 0.05, w: Math.max(0.04, trackW * v / 20), h: 0.08, fill: { color: i === 0 ? GOLD : BAR }, line: { type: "none" } });
    t(`${v.toFixed(1)} s`, { x: trackX + trackW + 0.05, y, w: 0.43, h: 0.18, fontSize: 8.5, color: MUTED, align: "right", valign: "middle" });
  });

  // 05 Execution & pitch
  line(0.6, 6.6, 12.13, 0);
  t([
    { text: "05  EXECUTION & PITCH   ", options: { bold: true, color: GOLD, charSpacing: 1.2, fontSize: 9 } },
    { text: "Live on Aiven Runtime. Not a count after the match: proof for every second.", options: { fontFace: HEAD, italic: true, fontSize: 12.5, color: IVORY } },
  ], { x: 0.6, y: 6.72, w: 10.2, h: 0.3, valign: "middle" });
  t("*Pilot on our earlier prototype, partly circular; independent labelling next. Evidence: recorded broadcast replayed through the live pipeline. Repo: github.com/kaktamsdengami/accel_hack-",
    { x: 0.6, y: 7.06, w: 12.13, h: 0.22, fontSize: 8, color: DIM });
  t("Live dashboard ↗", { x: 10.9, y: 6.72, w: 1.83, h: 0.3, fontSize: 11, bold: true, color: GOLD, align: "right", valign: "middle",
    hyperlink: { url: "https://pixel-perfect-recreation-849.lovable.app/dashboard?match=m-ucl-demo" } });

  const out = process.argv[2];
  await pres.writeFile({ fileName: out });
  await applyTheme(out, THEME);
})();
