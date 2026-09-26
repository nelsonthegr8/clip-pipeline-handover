#!/usr/bin/env python3
"""
beat_detect.py — Find "crazy financial moment" beats in a YouTube video and
cut clean 30-second clips.

USAGE (run with system python that has youtube-transcript-api):
  /usr/bin/python3 beat_detect.py "https://youtube.com/watch?v=VIDEO_ID" [--clip-len 30] [--max-clips 20] [--cut video.mp4] [--outdir /tmp/clips]

PIPELINE:
  1. Fetch auto/manual transcript (youtube-transcript-api, en fallback any)
  2. Parse timestamped segments
  3. Score lines: money amounts + financial-shock words + host reactions
  4. Cluster nearby hot lines into "moments"
  5. Snap each moment to a transcript line boundary (no mid-word starts)
  6. Optionally cut clips with ffmpeg (re-encode = frame-accurate, fade-out tail)

OUTPUT:
  <outdir>/clips.json  — full clip list with timestamps + peak quotes
  <outdir>/clip_NN_<ts>.mp4 — cut clips (only with --cut)
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

MONEY_RE = re.compile(r"\$\s?[\d,]+(?:\.\d{2})?")

FIN_SHOCK = [
    "garnish", "garnishing", "repossess", "repossession", "repoed", "collections",
    "overdraft", "past due", "back due", "interest rate", "underwater",
    "bankruptcy", "bankrupt", "mortgage", "payment", "debt", "owe", "owed",
    "foreclos", "charged off", "default", "take home", "take-home", "eating out",
    "months behind", "behind on",
]
# Lines where the HOST is giving advice (not a guest "crazy problem" beat)
ADVICE_RE = re.compile(
    r"pay off|pay it off|start (a |an )?(emergency|clean|new)|build (an |up )?|"
    r"sell the house|you need to|kick (him|her|them) out|learn to budget|do a budget|"
    r"set the rest|focus on your career|go get (that|a) job|put the house up|"
    r"debt consolidation|consolidate",
    re.I,
)
REACTION = re.compile(
    r"what the|oh my god|oh my gosh|what are we doing|come on|jeez|whoa|"
    r"what is wrong|what is possibly wrong|stupidest|no excuse|that is insane|insane",
    re.I,
)


def parse_ts(raw: str) -> int:
    parts = raw.split(":")
    if len(parts) == 3:
        return int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
    return int(parts[0]) * 60 + int(parts[1])


def fmt_t(s: int) -> str:
    return f"{s // 60:02d}:{s % 60:02d}"


def get_segments(video_id: str):
    from youtube_transcript_api import YouTubeTranscriptApi
    api = YouTubeTranscriptApi()
    try:
        result = api.fetch(video_id, languages=["en", "en-US"])
    except Exception:
        result = api.fetch(video_id)
    return [(int(seg.start), (seg.text or "").strip()) for seg in result]


def detect(segs, clip_len=30, gap=40, lead=4):
    hot = []
    for i, (secs, content) in enumerate(segs):
        c = content.lower()
        score = 0
        if MONEY_RE.search(content):
            score += 2
        if any(w in c for w in FIN_SHOCK):
            score += 3
        if REACTION.search(content):
            score += 2
        if ADVICE_RE.search(content):
            score -= 2
        if score >= 3:
            hot.append((i, secs, content, score))

    clusters = []
    for i, secs, content, score in hot:
        if clusters and secs - clusters[-1]["end"] <= gap:
            cl = clusters[-1]
            cl["end"] = secs
            cl["items"].append((i, secs, content, score))
        else:
            clusters.append({"start": secs, "end": secs, "items": [(i, secs, content, score)]})

    clips = []
    for cl in clusters:
        peak = max(cl["items"], key=lambda x: (x[3], bool(MONEY_RE.search(x[2])), len(x[2])))
        pidx, psecs, ptext, _ = peak
        # snap start to a line boundary: a line starting within `lead` s before the peak
        target = psecs - lead
        best_start = psecs
        for j in range(pidx, -1, -1):
            ts = segs[j][0]
            if ts <= target and (target - ts) <= lead + 4:
                best_start = ts
                break
            if (psecs - ts) > lead + 10:
                break
        end = best_start + clip_len
        ctx = " ".join(segs[k][1] for k in range(max(0, pidx - 3), min(len(segs), pidx + 4)))
        clips.append({
            "start": best_start,
            "end": end,
            "start_ts": fmt_t(best_start),
            "end_ts": fmt_t(end),
            "peak": ptext.strip()[:160],
            "context": ctx[:280],
        })
        # drop clips that overlap the previous one by > 25% of their length
        if clips and len(clips) >= 2:
            prev = clips[-2]
            overlap = min(prev["end"], end) - max(prev["start"], best_start)
            if overlap > clip_len * 0.25:
                clips.pop()

    return clips


def cut(video, clip, idx, outdir: Path):
    name = f"clip_{idx:02d}_{clip['start_ts'].replace(':', '-')}.mp4"
    out = outdir / name
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", str(clip["start"]), "-i", video, "-t", str(clip["end"] - clip["start"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
        "-c:a", "aac", "-b:a", "128k",
        "-af", f"afade=t=out:st={clip['end'] - clip['start'] - 0.4}:d=0.4",
        "-vf", f"fade=t=out:st={clip['end'] - clip['start'] - 0.3}:d=0.3",
        str(out),
    ]
    subprocess.run(cmd, check=True)
    clip["file"] = str(out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("url")
    ap.add_argument("--clip-len", type=int, default=30)
    ap.add_argument("--gap", type=int, default=40)
    ap.add_argument("--lead", type=int, default=4)
    ap.add_argument("--max-clips", type=int, default=25)
    ap.add_argument("--cut", metavar="VIDEO_MP4", help="source video file to cut from")
    ap.add_argument("--outdir", default="/tmp/clips")
    args = ap.parse_args()

    m = re.search(r"(?:v=|youtu\.be/|shorts/)([\w-]{11})", args.url)
    if not m:
        m = re.fullmatch(r"([\w-]{11})", args.url.strip())
    video_id = m.group(1) if m else None
    if not video_id:
        sys.exit("could not parse video id from url")

    print(f"[1/3] transcript for {video_id} ...", file=sys.stderr)
    segs = get_segments(video_id)
    print(f"      {len(segs)} segments, {fmt_t(segs[-1][0])} runtime", file=sys.stderr)

    print("[2/3] detecting beats ...", file=sys.stderr)
    clips = detect(segs, clip_len=args.clip_len, gap=args.gap, lead=args.lead)[: args.max_clips]

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    meta = {"video_id": video_id, "runtime": fmt_t(segs[-1][0]), "clips": clips}
    (outdir / "clips.json").write_text(json.dumps(meta, indent=2))
    print(f"      {len(clips)} clips -> {outdir / 'clips.json'}", file=sys.stderr)

    if args.cut:
        print(f"[3/3] cutting {len(clips)} clips ...", file=sys.stderr)
        for i, clip in enumerate(clips, 1):
            cut(args.cut, clip, i, outdir)
            print(f"      {i:2}. {clip['start_ts']}-{clip['end_ts']}  {clip['peak'][:70]}", file=sys.stderr)
    else:
        for i, clip in enumerate(clips, 1):
            print(f"{i:2}. [{clip['start_ts']}-{clip['end_ts']}] {clip['peak'][:100]}", file=sys.stderr)

    print(json.dumps(meta, indent=2))


if __name__ == "__main__":
    main()
