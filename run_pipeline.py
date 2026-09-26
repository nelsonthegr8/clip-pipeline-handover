#!/usr/bin/env python3
"""
run_pipeline.py — ONE-CALL entrypoint for the finance reaction clip pipeline.
Designed to be invoked by n8n (Execute Command node) or from the shell.

INPUT (exactly one of):
  --url      "https://youtube.com/watch?v=VIDEO_ID"   one known video
  --creator  @CalebHammer                             latest N uploads from a channel
  --search   "financial audit"                        list viral candidates (no download)

OUTPUT (stdout — always a single JSON object, safe for n8n to parse):
  {
    "ok": true,
    "mode": "url" | "creator" | "search",
    "videos": [
      {
        "video_id": "...", "title": "...", "channel": "...",
        "url": "https://youtube.com/watch?v=...",
        "clips_found": 14, "clips_cut": 14,
        "clips": [{"n": 1, "start_ts": "02:20", "end_ts": "02:50",
                    "peak": "garnish my wages...", "file": "...", "size_kb": 1024}],
        "result_file": "out/<id>/result.json"
      }
    ],
    "errors": [...]
  }

Per-video artifacts:  out/<video_id>/clips.json  +  clipNN_*.mp4  +  result.json
Video cache:          downloads/<video_id>.<ext>  (skips re-download)

EXIT CODES: 0 = all requested videos processed (some may be in "errors"), 1 = total failure.
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

import beat_detect  # noqa: E402  (same dir)

PY = sys.executable or "/usr/bin/python3"
YT = [PY, "-m", "yt_dlp", "--no-playlist", "--js-runtimes", "node"]


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def video_id_from(url: str) -> str | None:
    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", url)
    return m.group(1) if m else (url.strip() if re.fullmatch(r"[\w-]{11}", url.strip()) else None)


def yt_print(url: str, template: str, head: int | None = None):
    """yt-dlp flat/quick metadata print; returns list of lines."""
    cmd = YT + ["--flat-playlist", "--print", template, url]
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"yt-dlp failed for {url}: {r.stderr.strip()[:300]}")
    lines = [ln for ln in r.stdout.splitlines() if ln.strip() and not ln.startswith(("WARNING", "ERROR"))]
    return lines[:head] if head else lines


def list_creator_videos(handle: str, n: int):
    handle = handle.lstrip("@")
    url = f"https://www.youtube.com/@{handle}/videos"
    out = []
    for ln in yt_print(url, "%(id)s|%(title).120s", n):
        if "|" not in ln:
            continue
        vid, title = ln.split("|", 1)
        out.append({"video_id": vid.strip(), "title": title.strip()})
    return out


def list_search_candidates(query: str, n: int, sp: str):
    url = f"https://www.youtube.com/results?search_query={query.replace(' ', '+')}&sp={sp}"
    out = []
    for ln in yt_print(url, "%(id)s|%(view_count)s|%(title).120s", n * 3):
        if "|" not in ln:
            continue
        parts = ln.split("|", 2)
        if len(parts) < 3:
            continue
        out.append({"video_id": parts[0].strip(),
                    "view_count": parts[1].strip(),
                    "title": parts[2].strip()})
        if len(out) >= n:
            break
    return out


def find_cached(vid: str) -> Path | None:
    for ext in ("mp4", "webm", "mkv", "mp4.webm"):
        p = BASE / "downloads" / f"{vid}.{ext}"
        if p.exists() and p.stat().st_size > 1_000_000:
            return p
    return None


def download(vid: str) -> Path:
    cached = find_cached(vid)
    if cached:
        log(f"  [cache] {cached.name}")
        return cached
    (BASE / "downloads").mkdir(exist_ok=True)
    cmd = YT + ["-f", "bv*[height<=480]+ba/b[height<=480]/b",
                "-o", str(BASE / "downloads" / f"%(id)s.%(ext)s"),
                f"https://www.youtube.com/watch?v={vid}"]
    log(f"  [dl] downloading {vid} (480p) ...")
    r = run(cmd)
    if r.returncode != 0:
        raise RuntimeError(f"download failed: {r.stderr.strip()[:300]}")
    return find_cached(vid) or (_ for _ in ()).throw(RuntimeError("downloaded file not found"))


def meta(vid: str) -> dict:
    r = run(YT + ["--skip-download", "--print", "%(title)s|%(channel)s",
                  f"https://www.youtube.com/watch?v={vid}"])
    if r.returncode == 0 and "|" in r.stdout:
        title, channel = r.stdout.strip().split("|", 1)
        return {"title": title.strip(), "channel": channel.strip()}
    return {"title": "", "channel": ""}


def cut_clip(vid_file: Path, clip: dict, idx: int, outdir: Path) -> Path | None:
    name = f"clip{idx:02d}_{clip['start_ts'].replace(':', '-')}-{clip['end_ts'].replace(':', '-')}_{re.sub(r'[^a-z0-9]+', '-', clip['peak'].lower().split()[0] if clip['peak'] else 'beat')[:40]}.mp4"
    out = outdir / name
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(clip["start"]), "-i", str(vid_file),
             "-t", str(clip["end"] - clip["start"]), "-c", "copy", str(out)])
    if r.returncode == 0 and out.exists() and out.stat().st_size > 50_000:
        return out
    # fall back to re-encode if stream copy failed
    r = run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
             "-ss", str(clip["start"]), "-i", str(vid_file),
             "-t", str(clip["end"] - clip["start"]),
             "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
             "-c:a", "aac", "-b:a", "128k", str(out)])
    if r.returncode == 0 and out.exists() and out.stat().st_size > 50_000:
        return out
    return None


def process_video(vid: str, title: str, channel: str, args) -> dict:
    log(f"[{vid}] processing ...")
    vid_file = download(vid)
    if not title or not channel:
        m = meta(vid)
        title, channel = m.get("title", ""), m.get("channel", "")

    log(f"[{vid}] transcript + beat detection ...")
    segs = beat_detect.get_segments(vid)
    clips = beat_detect.detect(segs, clip_len=args.clip_len, gap=args.gap, lead=args.lead)[: args.max_clips]

    outdir = BASE / "out" / vid
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "clips.json").write_text(json.dumps({"video_id": vid, "clips": clips}, indent=2))

    cut_results = []
    if not args.no_cut:
        for i, clip in enumerate(clips, 1):
            f = cut_clip(vid_file, clip, i, outdir)
            cut_results.append(f)
    else:
        cut_results = [None] * len(clips)

    clip_out = []
    for i, (clip, f) in enumerate(zip(clips, cut_results), 1):
        clip_out.append({
            "n": i, "start_ts": clip["start_ts"], "end_ts": clip["end_ts"],
            "peak": clip["peak"][:160],
            "file": str(f) if f else None,
            "size_kb": (f.stat().st_size // 1024) if f else 0,
        })

    result = {
        "video_id": vid, "title": title, "channel": channel,
        "url": f"https://www.youtube.com/watch?v={vid}",
        "runtime": beat_detect.fmt_t(segs[-1][0]) if segs else "?",
        "clips_found": len(clips), "clips_cut": sum(1 for f in cut_results if f),
        "clips": clip_out,
        "result_file": str(outdir / "result.json"),
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    (outdir / "result.json").write_text(json.dumps(result, indent=2))
    log(f"[{vid}] done: {len(clips)} beats, {result['clips_cut']} cut")
    return result


def main():
    ap = argparse.ArgumentParser(description="Finance reaction clip pipeline (n8n-ready)")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--url", help="YouTube video URL or 11-char id")
    src.add_argument("--creator", help="channel handle, e.g. @CalebHammer")
    src.add_argument("--search", help="search query for viral discovery (lists candidates only)")
    ap.add_argument("--max-videos", type=int, default=3, help="max videos to process (creator mode)")
    ap.add_argument("--max-clips", type=int, default=25)
    ap.add_argument("--clip-len", type=int, default=30)
    ap.add_argument("--gap", type=int, default=40)
    ap.add_argument("--lead", type=int, default=4)
    ap.add_argument("--no-cut", action="store_true", help="detect beats but don't cut mp4s")
    ap.add_argument("--search-sp", default="CAMSBAgCEAE%3D",
                    help="YouTube search filter (default = this week, sorted by views)")
    args = ap.parse_args()

    out = {"ok": True, "mode": None, "videos": [], "errors": []}

    try:
        if args.search:
            out["mode"] = "search"
            cands = list_search_candidates(args.search, args.max_videos, args.search_sp)
            out["videos"] = cands
            log(f"[search] {len(cands)} candidates for '{args.search}'")
            log("  pick one, then re-run with --url to cut clips")

        elif args.creator:
            out["mode"] = "creator"
            vids = list_creator_videos(args.creator, args.max_videos)
            if not vids:
                raise RuntimeError(f"no videos found for {args.creator}")
            for v in vids:
                log(f"[{args.creator}] {v['video_id']} — {v['title'][:60]}")
                try:
                    out["videos"].append(process_video(v["video_id"], v["title"], "", args))
                except Exception as e:
                    out["errors"].append({"video_id": v["video_id"], "error": str(e)[:300]})
                    log(f"[{v['video_id']}] ERROR: {e}")

        else:
            out["mode"] = "url"
            vid = video_id_from(args.url)
            if not vid:
                raise RuntimeError(f"could not parse video id from {args.url}")
            m = meta(vid)
            out["videos"].append(process_video(vid, m.get("title", ""), m.get("channel", ""), args))

    except Exception as e:
        out["ok"] = False
        out["errors"].append({"error": str(e)[:300]})
        log(f"FATAL: {e}")

    print(json.dumps(out, indent=2))
    sys.exit(0 if out["videos"] or not out["errors"] else 1)


if __name__ == "__main__":
    main()
