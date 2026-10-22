"""Voice-over for the demo video: one MP3 per scene of docs/PITCH.md via ElevenLabs.

    python scripts/voiceover.py [--voice nPczCjzI2devNBz1zQrb] [--model eleven_multilingual_v2] [--only 3]

Reads scenes ("**0:15-0:40 | Title** (shot)" followed by "> text" lines) from the voice-over section,
writes data/voiceover/NN-slug.mp3 and data/voiceover/scenes.json (slot, text, audio seconds) for Remotion.
Needs ELEVENLABS_API_KEY in .env (text_to_speech permission is enough; voice ids are passed directly).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "voiceover"
SCENE = re.compile(r"^\*\*(\d+:\d\d)-(\d+:\d\d) \| ([^*]+)\*\*(.*)$")
KBPS = 128  # output_format below; duration = bytes * 8 / bitrate (CBR)


def _key() -> str:
    if os.environ.get("ELEVENLABS_API_KEY"):
        return os.environ["ELEVENLABS_API_KEY"]
    for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
        if line.startswith("ELEVENLABS_API_KEY="):
            return line.split("=", 1)[1].strip()
    raise SystemExit("ELEVENLABS_API_KEY missing in .env")


def _secs(mmss: str) -> int:
    m, s = mmss.split(":")
    return int(m) * 60 + int(s)


def scenes(pitch: Path) -> list[dict]:
    text = pitch.read_text(encoding="utf-8").split("voice-over script", 1)[1]
    out: list[dict] = []
    for line in text.splitlines():
        if m := SCENE.match(line.strip()):
            out.append({"start": _secs(m[1]), "end": _secs(m[2]), "title": m[3].strip(),
                        "shot": m[4].strip(" ()"), "text": ""})
        elif line.startswith(">") and out:
            out[-1]["text"] = (out[-1]["text"] + " " + line.lstrip("> ").strip()).strip()
    return out


def tts(text: str, voice: str, model: str, key: str) -> bytes:
    req = urllib.request.Request(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice}?output_format=mp3_44100_{KBPS}",
        data=json.dumps({"text": text, "model_id": model,
                         "voice_settings": {"stability": 0.5, "similarity_boost": 0.75}}).encode(),
        headers={"xi-api-key": key, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--pitch", default=str(ROOT / "docs" / "PITCH.md"))
    ap.add_argument("--voice", default="nPczCjzI2devNBz1zQrb")  # premade "Brian", calm narrator
    ap.add_argument("--model", default="eleven_multilingual_v2")
    ap.add_argument("--only", type=int, help="regenerate one scene (1-based)")
    a = ap.parse_args()
    key, sc = _key(), scenes(Path(a.pitch))
    OUT.mkdir(parents=True, exist_ok=True)
    for i, s in enumerate(sc, 1):
        s["file"] = f"{i:02d}-{re.sub(r'[^a-z0-9]+', '-', s['title'].lower()).strip('-')}.mp3"
        path = OUT / s["file"]
        if a.only in (None, i):
            path.write_bytes(tts(s["text"], a.voice, a.model, key))
        s["audio_s"] = round(path.stat().st_size * 8 / (KBPS * 1000), 1) if path.exists() else None
        slot = s["end"] - s["start"]
        flag = "  <-- longer than slot" if s["audio_s"] and s["audio_s"] > slot else ""
        print(f"{i:02d} {s['title']:<22} slot {slot:>3}s  audio {s['audio_s']}s{flag}")
    (OUT / "scenes.json").write_text(json.dumps(sc, indent=2), encoding="utf-8")
    print(f"total audio {sum(s['audio_s'] or 0 for s in sc):.1f}s -> {OUT}")


if __name__ == "__main__":
    main()
