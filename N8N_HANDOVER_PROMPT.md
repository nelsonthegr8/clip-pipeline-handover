# Handover prompt for the n8n Setup Assistant

> PASTE EVERYTHING BELOW (starting at "You are building…") into the n8n setup
> assistant. Before you do:
> 1. Copy the whole `clip-pipeline/` folder into your n8n instance so the assistant
>    can read it — bind-mount it into the n8n container at `/opt/clip-pipeline`
>    (if n8n is a container on the same Proxmox host: add the host path
>    `/home/nelsonthegr8/clip-pipeline` as a mount at `/opt/clip-pipeline`),
>    or scp it to `/opt/clip-pipeline` on the n8n server.
> 2. Make sure the n8n container (or host) has: python3, pip, ffmpeg, and node.
>    If not, tell the assistant to install them as part of setup.

---

You are building a content-sourcing workflow for a finance reaction YouTube
channel. The goal: automatically find "crazy financial problem" moments in
finance-niche YouTube videos and cut ~30-second clips from each, so the owner can
batch-record reaction videos (target: ~5 reactions per recording session, ~1 month
of backlog). The benchmark channel for volume/format is @Tawktoembwoi.

**A verified reference implementation already exists at `/opt/clip-pipeline`.**
Read `HANDOVER.md` there FIRST — it documents the environment setup, the exact
commands, the output JSON contract, the tuned detection algorithm, and known
pitfalls. Then read `run_pipeline.py` and `beat_detect.py`. Your job is to wire
n8n around that code, NOT to rewrite the detection logic.

## What the reference code does (one command, three modes)

```
/usr/bin/python3 /opt/clip-pipeline/run_pipeline.py --url "<yt-url-or-id>"
    → downloads (480p, cached), fetches transcript, detects beats, cuts clips,
      prints JSON on stdout: {ok, mode, videos:[{video_id,title,channel,url,
      clips_found,clips_cut,clips:[{n,start_ts,end_ts,peak,file,size_kb}],result_file}],errors}
/usr/bin/python3 /opt/clip-pipeline/run_pipeline.py --creator @Handle --max-videos 3
    → same, but for the newest N uploads of a channel
/usr/bin/python3 /opt/clip-pipeline/run_pipeline.py --search "financial audit" --max-videos 10
    → lists this week's top-viewed search candidates ONLY (no downloads):
      {videos:[{video_id,view_count,title}]} — meant for an LLM curation step
```

Logs go to stderr; stdout is always one JSON object. Exit 0 = processed (see
`errors[]`), 1 = total failure.

## Setup step 1 — verify the environment (do this first, report what's missing)

Run each via Execute Command (or ask the user):
1. `python3 --version` and `node --version` — both must exist.
2. `python3 -c "import youtube_transcript_api, yt_dlp"` — if this fails run:
   `pip install --break-system-packages -r /opt/clip-pipeline/requirements.txt`
   (use plain `pip install -r ...` if the flag is rejected).
3. `ffmpeg -version` — must exist; if not, install it.
4. Smoke test: `python3 /opt/clip-pipeline/run_pipeline.py --url _WBuNnEUJ9Y --no-cut`
   — MUST return `"ok": true` and `clips_found` >= 15. If it fails, debug before
   building any workflow (usually: missing python dep or ffmpeg).

## Setup step 2 — build these workflows

**WF 1: "Watch list — new episodes" (Schedule Trigger, daily):**
1. Code node: read `/opt/clip-pipeline/creators.json` → list of handles.
2. For each handle, Execute Command:
   `python3 /opt/clip-pipeline/run_pipeline.py --creator <handle> --max-videos 1 --no-cut`
   (cheap probe — no download; just tells us if the newest episode is new to us).
3. Code node (dedupe): keep a file `/opt/clip-pipeline/processed.txt` (one video_id
   per line). If the newest video_id is already in it → stop for this creator.
4. If new → Execute Command: full run `--creator <handle> --max-videos 1` (downloads,
   detects, cuts). Parse stdout JSON.
5. Code node: append the processed video_id(s) to `processed.txt`.
6. Notification node (Discord Webhook — ask the user for the webhook URL, use a
   placeholder until given): one message per new episode:
   `🎬 New episode cut: <title> (<channel>) — <clips_found> beats, <clips_cut> clips`
   followed by a numbered list: `1. [02:20-02:50] garnish my wages. So I'm missing about`
   (use each clip's `start_ts`/`end_ts` + `peak`), then the `result_file` path.

**WF 2: "Viral finance discovery" (Schedule Trigger weekly, or Manual Trigger):**
1. Code node: read `search_queries` from `creators.json`.
2. For each query, Execute Command:
   `python3 /opt/clip-pipeline/run_pipeline.py --search "<query>" --max-videos 10`.
3. LLM node (use the configured self-hosted model): given the combined candidate
   list, prompt it: "You are curating source videos for a finance reaction channel
   (reactions to couples' financial disasters — think Financial Audit). Pick the
   top 3 videos worth reacting to. For each: title, video_id, one-line reason.
   Skip anything that isn't personal finance content (city councils, corporate
   audit reports, clip-compilation channels)."
4. Notification node (Discord Webhook): send the 3 picks with their YouTube URLs
   and the note "reply with a number and I'll cut the clips".
5. (Optional, build after the user confirms the format works) a Webhook
   "cut-this" endpoint: body `{video_id}` → Execute Command `--url <id>` →
   notification with the full clip list (same format as WF 1 step 6).

**WF 3: "Manual cut" (Webhook Trigger):**
Body contains a YouTube URL or 11-char id → Execute Command `--url <it>` →
Discord notification with the clip list (same format). This is the escape hatch
for any video the user finds themselves.

## Hard rules (learned from production runs — do not "improve" these)

- Never fetch subtitles via yt-dlp (`--write-auto-subs`) — YouTube 429-blocks
  that from datacenter IPs. Transcripts MUST come from `youtube-transcript-api`
  (already wired into `beat_detect.py`).
- Never rewrite the scoring in `beat_detect.py` (money +2, shock word +3, reaction
  +2, host-advice −2, hot ≥ 3). If the user wants different clip taste, change
  constants in that file deliberately, not via prompt-tuning in n8n.
- Invoke yt-dlp as `python3 -m yt_dlp`, always with `--js-runtimes node`
  (already done inside run_pipeline.py — don't add parallel yt-dlp calls
  without that flag).
- Parse stdout only; stderr is log noise. Always check `ok` and `errors[]`.
- 480p downloads are deliberate (reaction overlay is the star). Don't bump quality.
- Keep `run_pipeline.py` stateless about "what's been processed" — dedupe lives
  in n8n + `processed.txt` only.

## Verify before declaring done

1. WF 3 against a known URL → clip list arrives in Discord with sensible
   timestamps and hook quotes.
2. WF 2 → LLM picks are actually finance content.
3. WF 1 twice in a row → second run is a no-op (dedupe works).
4. Show the user a sample Discord notification so they can approve the format.

Ask the user for: the Discord webhook URL(s) for notifications, and confirmation
of the mount path if `/opt/clip-pipeline` isn't visible.
