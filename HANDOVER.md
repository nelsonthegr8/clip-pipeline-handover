# Finance Reaction Clip Pipeline — Handover

Verified working end-to-end on 2026-09-25. Purpose: given a YouTube video from the
finance niche (e.g. Caleb Hammer *Financial Audit*), automatically find the ~15–25
"crazy financial problem" moments and cut 30-second clips from each — the raw
material for a reaction channel (format benchmark: @Tawktoembwoi).

**Verified benchmark:** 90-min Financial Audit episode (`_WBuNnEUJ9Y`, "She Needs To
Divorce Him") → **21 detected beats, 21 clips cut in ~40s** (after the one-time 245MB
download). Densest zone = final ~20 min where the financial documents come out.
A single 90-min episode ≈ a week of reaction content.

---

## File map

```
clip-pipeline/
├── run_pipeline.py     ← THE entrypoint (n8n calls this one thing)
├── beat_detect.py      ← transcript → score → cluster → beat list (imported by run_pipeline)
├── requirements.txt    ← youtube-transcript-api, yt-dlp
├── creators.json       ← source creator handles + search queries (edit this)
├── downloads/          ← video cache (skip re-download if file present > 1MB)
├── out/<video_id>/     ← per-video: clips.json, clipNN_*.mp4, result.json
└── HANDOVER.md         ← this file
```

## Environment setup (host OR n8n container)

```bash
# 1. Python deps (system python3.12 on the reference host; --break-system-packages because
#    the venv python can't see user-site packages and has no pip — use /usr/bin/python3)
pip install --break-system-packages -r requirements.txt

# 2. ffmpeg (required for cuts)
#    apt-get install -y ffmpeg   (or: apk add ffmpeg)

# 3. Node.js — yt-dlp wants a JS runtime for YouTube extraction (deno preferred, node works):
node --version   # v22+ fine; run_pipeline.py passes --js-runtimes node automatically
```

Everything else is plain stdlib. No API keys, no auth — all three discovery paths
(transcripts, channel listings, search) work unauthenticated from a datacenter IP.

## The three invocation modes

All output a single JSON object on **stdout** (logs go to stderr — safe for n8n to parse).

### 1. Cut clips from a known video
```bash
/usr/bin/python3 run_pipeline.py --url "https://youtube.com/watch?v=_WBuNnEUJ9Y"
# or just the id:  --url "_WBuNnEUJ9Y"
```
Downloads at 480p if not cached (plenty for reaction source — the facecam overlay is
the star), fetches the transcript, detects beats, stream-cuts clips (fast, no re-encode).

### 2. Latest uploads from a creator (the "watch list" flow)
```bash
/usr/bin/python3 run_pipeline.py --creator @CalebHammer --max-videos 3
```
Lists the channel's newest uploads via `yt-dlp --flat-playlist` (no full download),
then runs the full pipeline on the newest N. This is what a daily schedule trigger calls.

### 3. Viral discovery (list only — no downloads)
```bash
/usr/bin/python3 run_pipeline.py --search "financial audit" --max-videos 10
```
Returns this week's top-viewed YouTube results for the query (default filter
`sp=CAMSBAgCEAE%3D` = this week, sorted by views — tunable via `--search-sp`).
Designed for an LLM/human curation step: pick a candidate, then re-run with `--url`.

### Useful flags
`--no-cut` (detect only), `--max-clips 25`, `--clip-len 30`, `--gap 40`, `--lead 4`,
`--max-videos N`.

## Output contract (stdout JSON)

```json
{
  "ok": true,
  "mode": "url|creator|search",
  "videos": [
    {
      "video_id": "...", "title": "...", "channel": "...",
      "url": "https://youtube.com/watch?v=...",
      "clips_found": 21, "clips_cut": 21,
      "clips": [
        {"n": 1, "start_ts": "02:20", "end_ts": "02:50",
         "peak": "garnish my wages. So I'm missing about",
         "file": "/.../out/_WBuNnEUJ9Y/clip01_02-20-02-50_garnish.mp4",
         "size_kb": 1592}
      ],
      "result_file": "/.../out/<id>/result.json"
    }
  ],
  "errors": []
}
```

In **search mode** `videos[]` items are candidates instead: `{video_id, view_count, title}`.

## How beat detection works (tuned — don't change blindly)

Per transcript line, score:
- contains `$` amount → **+2**
- contains finance-shock word (garnish, repossess, collections, overdraft, past due,
  underwater, bankruptcy, mortgage, payment, debt, owe, foreclos, take-home, eating
  out, months behind, behind on, …) → **+3**
- contains host reaction ("what the", "oh my god", "stupidest", "come on", "whoa", …) → **+2**
- matches host-ADVICE pattern ("pay off", "start an emergency fund", "sell the house",
  "you need to", "debt consolidation", …) → **−2**
- **hot if score ≥ 3**

Hot lines cluster into "moments" (gap ≤ 40s). Each moment's peak quote = highest-scoring
line (ties → prefer lines with `$`). Clip start snaps to a transcript line boundary ~4s
before the peak so it never begins mid-word. Overlapping clips (>25%) are dropped.

**Tuning lesson (earned the hard way):** without the −2 advice penalty, the host's
"pay off your debt, start a clean new life" advice lines out-scored the guest's actual
problem reveals in peak selection and the output filled with advice instead of drama.
The user reacts to *bad decisions* — filter for the guest's crazy problem, not the
host's fix. Also: exclude non-financial drama beats (relationship/abuse) unless they
carry a dollar amount.

## n8n integration notes

- **Node to use:** *Execute Command* (n8n-nodes-base.executeCommand). Working directory
  should be the pipeline dir (or pass absolute paths — `run_pipeline.py` resolves its own
  base dir, so it works from anywhere; only the `downloads/`/`out/` locations move with it).
- **Parse stdout** with a Code node: `JSON.parse($json.exitCode === 0 ? $json.stdout : JSON.stringify({ok:false, stderr:$json.stderr}))`.
- **Deduplication for the watch-list flow:** keep a `processed.txt` (one video_id per
  line) in the pipeline dir. Before calling `--creator`/`--search`, a Code node reads it,
  filters out already-seen ids, and only calls `run_pipeline.py --url` for new ones.
  After success, append the ids. (run_pipeline.py does NOT do this itself — keep it dumb.)
- **Suggested workflows:**
  1. **Watch list (daily schedule):** read `creators.json` → for each handle, lightweight
     "newest uploads" check (yt-dlp flat list, or just call `--creator` with `--max-videos 1`
     + `--no-cut` first to see if there's anything new) → dedupe vs `processed.txt` →
     full run on new episodes → Discord webhook with the clip list.
  2. **Viral discovery (weekly schedule or on-demand):** `--search` per query in
     `creators.json` → LLM node (your self-hosted model) picks top 3 candidates with
     one-line reasons → Discord webhook for approval → manual/webhook "cut this" → `--url`.
  3. **Manual cut (webhook):** body contains a URL → `--url` → Discord webhook with results.
- **Discord notification format that worked well** (from the reference run): numbered
  list `N. [MM:SS-MM:SS] — hook quote`, one line per clip. Attach the clip files if the
  webhook path supports attachments, otherwise give file paths.
- **LLM-assisted curation (optional but recommended):** after `--search`, feed the
  candidate list to the LLM with the prompt "pick the videos a finance-reaction channel
  would get views from reacting to; reason in one line each; skip anything that isn't
  finance" — the raw search results contain noise (city council audits, clip channels).

## Pitfalls (all hit in production)

- **yt-dlp subtitles are 429-blocked from datacenter IPs** — that's why transcripts come
  from `youtube-transcript-api`, not `--write-auto-subs`. Never "fix" this by switching.
- **Use `/usr/bin/python3`** (system python) — the project venv python can't see
  user-site packages and has no pip.
- **No `yt-dlp` binary** — invoke as `python3 -m yt_dlp` (run_pipeline.py does).
- **JS runtime warning** from yt-dlp is harmless but fixed by `--js-runtimes node`
  (already in run_pipeline.py); keep node installed.
- **480p is deliberate** — reaction videos overlay a facecam; 1080p just bloats cache.
- **Stream-copy cuts** (`-c copy`) are not frame-accurate (cuts on keyframes) — totally
  fine for reaction source, which gets re-cut in the editor anyway. Re-encode fallback
  is built in if copy fails (webm→mp4 edge cases).
- **Transcript timestamps are ±1–2s** — clips intentionally start a line boundary before
  the peak, so the reveal lands inside the window. Verified acceptable by the user.
- **Signal density varies by episode** — report the clip COUNT and the dense-zone range,
  not just a flat list. Some episodes yield 5 beats, some 25+.

## Suggested next steps (not yet built)

- LLM title/hook generation per clip (for YT Shorts-style uploads).
- Auto-thumbnail crop at the peak timestamp.
- Per-creator beat-style profiles (some shows are numbers-heavy, some story-heavy).
- TikTok side of the pipeline (separate problem — see tiktok-content-discovery skill;
  YouTube needs no scraping at all).
- Copyright: reaction format = facecam + commentary over short clips (fair-use posture,
  same as every finance reaction channel). Don't re-upload long unmodified segments.
