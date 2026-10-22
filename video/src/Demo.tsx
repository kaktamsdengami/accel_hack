import React from "react";
import {
  AbsoluteFill, Audio, Easing, Img, Sequence, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig,
} from "remotion";
import shots from "../public/shots/shots.json";
import scenes from "../public/voiceover/scenes.json";

export const FPS = 30;
export const TOTAL_S = 180;
const VW = 1600; // browser-window viewport inside the 1920x1080 frame
const VH = 850;
const BAR = 44;
const ACCENT = "#ff4d3d"; // dashboard red
const INK = "#f4f2ef";
const MUTED = "#9a958f";
const FONT = "Inter, 'Segoe UI', Arial, sans-serif";
const DISPLAY = "'Bebas Neue', Oswald, Impact, 'Arial Narrow', sans-serif";
const BG = "radial-gradient(1100px 650px at 10% -10%, rgba(255,77,61,0.16), rgba(255,77,61,0) 60%), radial-gradient(900px 600px at 105% 110%, rgba(255,77,61,0.10), rgba(255,77,61,0) 60%), #0b0b0d";
const SMOOTH = Easing.bezier(0.65, 0, 0.35, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

type Box = { x: number; y: number; w: number; h: number };
type ShotName = keyof typeof shots;
type Key = { at: number; shot: ShotName; box: string; caption?: string; zoom?: number };

const CRITERIA: Record<string, string> = {
  C1: "Customer problem & value",
  C2: "Agent capability",
  C3: "Data at the core",
  C4: "Evidence it works",
  C5: "Execution & pitch",
};

const URLS: Record<string, string> = {
  landing: "pixel-perfect-recreation-849.lovable.app",
  dash: "pixel-perfect-recreation-849.lovable.app/dashboard",
  agents: "pixel-perfect-recreation-849.lovable.app/dashboard · Agents",
  ask0: "pixel-perfect-recreation-849.lovable.app/dashboard · Ask",
  ask1: "pixel-perfect-recreation-849.lovable.app/dashboard · Ask",
  report: "pixel-perfect-recreation-849.lovable.app/report/b-fedex",
};

// Camera path per scene (seconds relative to scene start). Order = scenes in docs/PITCH.md.
const PLAN: { criteria: string[]; keys: Key[]; title?: boolean; overlay?: "run" | "close" }[] = [
  { criteria: ["C1"], title: true, keys: [
    { at: 3.6, shot: "landing", box: "hero", caption: "Clubs sell their stadium to sponsors" },
    { at: 8.5, shot: "landing", box: "problem", caption: "Exposure is paid for. Nobody measures it live." },
  ] },
  { criteria: ["C1", "C3"], keys: [
    { at: 0, shot: "dash", box: "header", caption: "Recorded UCL segment · 58 s" },
    { at: 4, shot: "dash", box: "kpis", caption: "Seconds per sponsor, per surface" },
    { at: 9, shot: "dash", box: "leaderboard", caption: "Sponsor leaderboard" },
    { at: 14, shot: "dash", box: "rotation", caption: "LED rotation: who was on the boards, and when" },
    { at: 18, shot: "dash", box: "surfaces", caption: "Surface breakdown" },
  ] },
  { criteria: ["C3", "C4"], keys: [
    { at: 0, shot: "dash", box: "evidence", caption: "Every second is backed by a frame" },
    { at: 7, shot: "dash", box: "coverage", zoom: 1.5, caption: "100 % measured · 0 s unmeasured" },
    { at: 12, shot: "report", box: "top", caption: "Sponsor report with intervals and evidence" },
  ] },
  { criteria: ["C2"], keys: [
    { at: 0, shot: "dash", box: "agents", caption: "Three agents, one team" },
    { at: 4, shot: "dash", box: "vision", caption: "Vision Reader · unknown stays unknown" },
    { at: 13, shot: "dash", box: "budget", caption: "Budget Guard · hard budget per match" },
    { at: 21, shot: "agents", box: "log", caption: "Every decision logged: model, confidence, cost, latency" },
  ] },
  { criteria: ["C2", "C3"], keys: [
    { at: 0, shot: "ask0", box: "ask", caption: "Sponsor Analyst · real agent on our data API" },
    { at: 5, shot: "ask1", box: "ask", caption: "Tool calls: getReport → getBrand" },
    { at: 12, shot: "ask1", box: "answer", zoom: 1.5, caption: "Live answer · FedEx · 20 s · far-side LED · 32–52 s" },
  ] },
  { criteria: ["C3"], keys: [
    { at: 0, shot: "dash", box: "datalayer", zoom: 1.5, caption: "Kafka → ClickHouse → Valkey → PostgreSQL" },
    { at: 7, shot: "landing", box: "aiven", caption: "Built on Aiven" },
    { at: 14, shot: "landing", box: "how", caption: "Ingest → read under budget → aggregate → report" },
  ] },
  { criteria: ["C4"], overlay: "run", keys: [
    { at: 0, shot: "dash", box: "budget", caption: "Measured run record" },
  ] },
  { criteria: ["C5"], overlay: "close", keys: [] },
];

const S = scenes as { start: number; end: number; title: string; file: string; text: string; audio_s: number }[];

function boxOf(k: Key): Box {
  const sh = shots[k.shot] as { w: number; h: number; boxes: Record<string, Box | null> };
  return sh.boxes[k.box] ?? { x: 0, y: 0, w: sh.w, h: VH };
}

function camera(b: Box, zoom?: number) {
  const pad = 40;
  const s = Math.min(VW / (b.w + 2 * pad), VH / (b.h + 2 * pad), zoom ?? 1.35);
  return { s, tx: VW / 2 - s * (b.x + b.w / 2), ty: VH / 2 - s * (b.y + b.h / 2) };
}

const Chapter: React.FC<{ ids: string[] }> = ({ ids }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [6, 26], [0, 1], { ...clamp, easing: SMOOTH });
  return (
    <div style={{ position: "absolute", top: 34, left: 60, display: "flex", alignItems: "center", gap: 18, opacity: o, transform: `translateX(${(1 - o) * -20}px)` }}>
      <span style={{ color: ACCENT, font: `400 52px ${DISPLAY}` }}>0{ids[0].slice(1)}</span>
      <span style={{ width: 44, height: 2, background: ACCENT }} />
      <span style={{ color: INK, font: `600 26px ${FONT}`, letterSpacing: 1.5, textTransform: "uppercase" }}>
        {ids.map((id) => CRITERIA[id]).join("  ·  ")}
      </span>
    </div>
  );
};

const Callout: React.FC<{ text: string; local: number }> = ({ text, local }) => {
  const o = interpolate(local, [8, 24], [0, 1], { ...clamp, easing: SMOOTH });
  return (
    <div style={{ position: "absolute", top: 28, right: 60, opacity: o, transform: `translateY(${(1 - o) * -12}px) scale(${0.96 + 0.04 * o})` }}>
      <div style={{
        background: "rgba(22,21,24,0.92)", color: INK, font: `600 26px ${FONT}`, padding: "12px 26px 12px 14px",
        borderRadius: 999, display: "flex", alignItems: "center", gap: 14, border: "1px solid rgba(255,255,255,0.10)",
        boxShadow: "0 10px 30px rgba(0,0,0,0.45)",
      }}>
        <span style={{ width: 28, height: 28, borderRadius: 99, background: ACCENT, flex: "none", boxShadow: `0 0 18px ${ACCENT}` }} />
        {text}
      </div>
    </div>
  );
};

// Burned-in subtitles: the scene's voice-over in short phrases, timed by length over the audio.
const Subtitles: React.FC<{ text: string; audio: number; offset: number }> = ({ text, audio, offset }) => {
  const f = useCurrentFrame();
  const parts = text.split(/(?<=[,.:;?!])\s+/).flatMap((p) => {
    const w = p.split(" ");
    const out: string[] = [];
    for (let i = 0; i < w.length; i += 8) out.push(w.slice(i, i + 8).join(" "));
    return out;
  });
  const total = parts.reduce((a, p) => a + p.length, 0);
  const t = (f - offset) / FPS;
  let acc = 0;
  for (const p of parts) {
    const start = (acc / total) * audio;
    acc += p.length;
    const end = (acc / total) * audio;
    if (t >= start && t < end) {
      const o = interpolate(t, [start, start + 0.15, end - 0.12, end], [0, 1, 1, 0.6], clamp);
      return (
        <div style={{ position: "absolute", bottom: 22, left: 0, right: 0, display: "flex", justifyContent: "center", opacity: o }}>
          <div style={{ background: "rgba(10,10,12,0.88)", color: INK, font: `600 32px ${FONT}`, padding: "9px 22px", borderRadius: 10 }}>{p}</div>
        </div>
      );
    }
  }
  return null;
};

// One page layer, positioned by the camera.
const Page: React.FC<{ k: Key; cam: { s: number; tx: number; ty: number }; opacity: number; glow: number }> = ({ k, cam, opacity, glow }) => {
  const sh = shots[k.shot];
  const bx = boxOf(k);
  return (
    <div style={{
      position: "absolute", left: 0, top: 0, width: sh.w, height: sh.h, opacity,
      transformOrigin: "0 0", transform: `translate(${cam.tx}px, ${cam.ty}px) scale(${cam.s})`,
    }}>
      <Img src={staticFile(sh.file)} style={{ width: sh.w, height: sh.h }} />
      <div style={{
        position: "absolute", left: bx.x - 6, top: bx.y - 6, width: bx.w + 12, height: bx.h + 12,
        border: `${2.5 / cam.s}px solid ${ACCENT}`, borderRadius: 12, opacity: glow * 0.85,
        boxShadow: `0 0 ${40 / cam.s}px rgba(255,77,61,0.35)`,
      }} />
    </div>
  );
};

const Camera: React.FC<{ keys: Key[]; enterAt: number }> = ({ keys, enterAt }) => {
  const f = useCurrentFrame();
  const { fps } = useVideoConfig();
  const t = f / FPS;
  let i = 0;
  while (i + 1 < keys.length && keys[i + 1].at <= t) i++;
  const cur = keys[i];
  const prev = keys[Math.max(0, i - 1)];
  const sameShot = prev.shot === cur.shot && i > 0;
  const p = i === 0 ? 1 : interpolate(t, [cur.at, cur.at + 1.3], [0, 1], { ...clamp, easing: SMOOTH });
  // slow continuous push-in while holding a shot
  const drift = 1 + 0.035 * interpolate(t - cur.at, [0, 9], [0, 1], { ...clamp, easing: Easing.out(Easing.quad) });
  const a = camera(boxOf(prev), prev.zoom);
  const b = camera(boxOf(cur), cur.zoom);
  const lerp = (x: number, y: number) => x + (y - x) * p;
  const withDrift = (c: { s: number; tx: number; ty: number }) => ({
    s: c.s * drift, tx: c.tx * drift - (VW / 2) * (drift - 1), ty: c.ty * drift - (VH / 2) * (drift - 1),
  });
  const glow = interpolate(t, [cur.at + 1.0, cur.at + 1.6], [0, 1], clamp);
  // window entrance
  const enter = spring({ frame: f - enterAt * fps, fps, config: { damping: 200, mass: 0.9 } });
  return (
    <AbsoluteFill>
      <div style={{
        position: "absolute", left: (1920 - VW) / 2, top: 120, width: VW, height: VH + BAR, borderRadius: 16,
        background: "#0f0f11", overflow: "hidden", border: "1px solid rgba(255,255,255,0.08)",
        boxShadow: "0 40px 120px rgba(0,0,0,0.6), 0 0 80px rgba(255,77,61,0.06)",
        opacity: enter, transform: `translateY(${(1 - enter) * 40}px) scale(${0.97 + 0.03 * enter})`,
      }}>
        <div style={{ height: BAR, background: "#18171a", display: "flex", alignItems: "center", padding: "0 18px", gap: 8, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          {["#ff5f57", "#febc2e", "#28c840"].map((c) => <span key={c} style={{ width: 12, height: 12, borderRadius: 99, background: c }} />)}
          <div style={{ margin: "0 auto", background: "#0f0f11", borderRadius: 8, padding: "5px 70px", color: MUTED, font: `500 16px ${FONT}` }}>
            {URLS[cur.shot]}
          </div>
        </div>
        <div style={{ position: "relative", width: VW, height: VH, overflow: "hidden" }}>
          {sameShot || i === 0 ? (
            <Page k={cur} cam={withDrift(i === 0 ? b : { s: lerp(a.s, b.s), tx: lerp(a.tx, b.tx), ty: lerp(a.ty, b.ty) })} opacity={1} glow={glow} />
          ) : (
            <>
              <Page k={prev} cam={withDrift(a)} opacity={1} glow={0} />
              <Page k={cur} cam={withDrift(b)} opacity={p} glow={glow} />
            </>
          )}
        </div>
      </div>
      {cur.caption && <Callout key={i} text={cur.caption} local={f - cur.at * FPS} />}
    </AbsoluteFill>
  );
};

const TitleCard: React.FC = () => {
  const f = useCurrentFrame();
  const o = interpolate(f, [0, 18, 92, 108], [0, 1, 1, 0], { ...clamp, easing: SMOOTH });
  const rise = interpolate(f, [0, 30], [24, 0], { ...clamp, easing: SMOOTH });
  return (
    <AbsoluteFill style={{ background: BG, alignItems: "center", justifyContent: "center", opacity: o }}>
      <div style={{ transform: `translateY(${rise}px)`, textAlign: "center" }}>
        <div style={{ color: ACCENT, font: `600 26px ${FONT}`, letterSpacing: 6 }}>FOOTBALL BROADCASTS. MEASURED.</div>
        <div style={{ color: INK, font: `400 200px ${DISPLAY}`, letterSpacing: 6, marginTop: 6 }}>
          <span style={{ color: ACCENT }}>/</span> EXPOSURE
        </div>
        <div style={{ color: MUTED, font: `500 40px ${FONT}` }}>Every second of sponsor exposure. Measured, priced, proven.</div>
      </div>
    </AbsoluteFill>
  );
};

const Stat: React.FC<{ v: string; l: string; d: number }> = ({ v, l, d }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [d, d + 16], [0, 1], { ...clamp, easing: SMOOTH });
  return (
    <div style={{ opacity: o, transform: `translateY(${(1 - o) * 18}px)`, minWidth: 330 }}>
      <div style={{ color: INK, font: `400 110px ${DISPLAY}`, lineHeight: 1 }}>{v}</div>
      <div style={{ color: MUTED, font: `500 28px ${FONT}` }}>{l}</div>
    </div>
  );
};

// Numbers: eval/runs/20261022T113439Z-ucl-vision-run.json (summary + derived).
const RunOverlay: React.FC = () => {
  const f = useCurrentFrame();
  const o = interpolate(f, [55, 80], [0, 1], { ...clamp, easing: SMOOTH });
  return (
    <AbsoluteFill style={{ background: `rgba(11,11,13,${0.93 * o})`, alignItems: "center", justifyContent: "center" }}>
      <div style={{ opacity: o, width: 1500 }}>
        <div style={{ color: ACCENT, font: `600 28px ${FONT}`, letterSpacing: 3 }}>
          MEASURED RUN · RECORDED UCL SEGMENT · 58 s · gpt-5-mini
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "34px 60px", marginTop: 36 }}>
          <Stat v="116" l="frames read" d={80} />
          <Stat v="81" l="sponsor sightings" d={95} />
          <Stat v="0" l="failed calls" d={110} />
          <Stat v="0" l="off-list brands" d={125} />
          <Stat v="$0.11" l="total model cost" d={170} />
          <Stat v="$0.12" l="per match-minute" d={200} />
          <Stat v="≈ $11" l="projected per 90-min match" d={235} />
        </div>
        <div style={{ color: MUTED, font: `500 24px ${FONT}`, marginTop: 40, opacity: interpolate(f, [260, 285], [0, 1], clamp) }}>
          Source: eval/runs/20261022T113439Z-ucl-vision-run.json · FedEx 20 s · PlayStation 12 s · Heineken 6.5 s · Mastercard 1 s
        </div>
      </div>
    </AbsoluteFill>
  );
};

const CloseCard: React.FC = () => {
  const f = useCurrentFrame();
  const rows: [string, string][] = [
    ["C1", "Clubs sell exposure nobody measures; the commercial manager gets seconds per sponsor"],
    ["C2", "Vision Reader · Budget Guard · Sponsor Analyst with tool calls"],
    ["C3", "Kafka → ClickHouse → Valkey → PostgreSQL on Aiven; every number cites a frame"],
    ["C4", "116 frames · 81 sightings · 0 failed calls · $0.11 for 58 s"],
    ["C5", "First users: club commercial teams preparing sponsor renewals"],
  ];
  const head = interpolate(f, [0, 24], [0, 1], { ...clamp, easing: SMOOTH });
  return (
    <AbsoluteFill style={{ background: BG, padding: "80px 150px", justifyContent: "center" }}>
      <div style={{ opacity: head, transform: `translateY(${(1 - head) * 20}px)` }}>
        <div style={{ color: INK, font: `400 140px ${DISPLAY}`, letterSpacing: 4, lineHeight: 1 }}>
          <span style={{ color: ACCENT }}>/</span> EXPOSURE
        </div>
        <div style={{ color: MUTED, font: `500 36px ${FONT}`, margin: "8px 0 44px" }}>
          Every second of sponsor exposure. Measured, priced, proven.
        </div>
      </div>
      {rows.map(([id, txt], i) => {
        const o = interpolate(f, [24 + i * 20, 40 + i * 20], [0, 1], { ...clamp, easing: SMOOTH });
        return (
          <div key={id} style={{ display: "flex", gap: 24, alignItems: "baseline", opacity: o, transform: `translateX(${(1 - o) * -16}px)`, marginBottom: 20 }}>
            <div style={{ color: ACCENT, font: `600 30px ${FONT}`, width: 440 }}>✓ {CRITERIA[id]}</div>
            <div style={{ color: INK, font: `500 30px ${FONT}` }}>{txt}</div>
          </div>
        );
      })}
      <div style={{ color: MUTED, font: `500 26px ${FONT}`, marginTop: 30, opacity: interpolate(f, [150, 170], [0, 1], clamp) }}>
        pixel-perfect-recreation-849.lovable.app · github.com/kaktamsdengami/accel_hack- · Next: logo memory
      </div>
    </AbsoluteFill>
  );
};

export const Demo: React.FC = () => (
  <AbsoluteFill style={{ background: BG }}>
    {PLAN.map((p, i) => {
      const sc = S[i];
      const from = Math.round(sc.start * FPS);
      const dur = Math.round((sc.end - sc.start) * FPS);
      const fade = (fr: number) => interpolate(fr, [0, 12, dur - 12, dur], [0, 1, 1, 0], clamp);
      return (
        <Sequence key={i} from={from} durationInFrames={dur}>
          <SceneFade fade={fade}>
            {p.keys.length > 0 && <Camera keys={p.keys} enterAt={p.title ? p.keys[0].at - 0.3 : 0} />}
            {p.title && <Sequence durationInFrames={110}><TitleCard /></Sequence>}
            {p.overlay === "run" && <RunOverlay />}
            {p.overlay === "close" && <CloseCard />}
            {p.overlay !== "close" && <Chapter ids={p.criteria} />}
            <Subtitles text={sc.text} audio={sc.audio_s} offset={9} />
          </SceneFade>
          <Sequence from={9}><Audio src={staticFile(`voiceover/${sc.file}`)} /></Sequence>
        </Sequence>
      );
    })}
  </AbsoluteFill>
);

const SceneFade: React.FC<{ fade: (f: number) => number; children: React.ReactNode }> = ({ fade, children }) => {
  const f = useCurrentFrame();
  return <AbsoluteFill style={{ opacity: fade(f) }}>{children}</AbsoluteFill>;
};
