# n8n Setup Assistant — split prompt (send ONE part at a time, wait for it to finish each)

## PART 1 — Setup check (send this first)

You are helping build a workflow for a finance-reaction YouTube channel: it finds
"crazy financial problem" moments in finance YouTube videos and cuts 30-second clips
for reaction videos.

FIRST TASK: create a workflow named "Clip pipeline — setup check" with a Manual
Trigger → one Execute Command node running this exact command:

```
git clone https://github.com/nelsonthegr8/clip-pipeline-handover.git /opt/clip-pipeline && (python3 -c "import youtube_transcript_api, yt_dlp" 2>/dev/null || pip install --break-system-packages -r /opt/clip-pipeline/requirements.txt) && ffmpeg -version | head -1 && node --version && python3 /opt/clip-pipeline/run_pipeline.py --url _WBuNnEUJ9Y --no-cut | tail -2
```

Run the workflow and paste the full output here. Expected: the last line shows
`"clips_found"` with a number 15 or higher. If anything fails, show me the error
and we fix that before building anything else.

---

## PART 2 — Workflow: daily watch list

PART 1 verified the environment. Now build workflow "Clip pipeline — watch list":
Schedule Trigger (daily, 09:00) → Code node → Execute Command → Code → Discord Webhook (URL: ASK ME).

- Code node #1 (named "load creators"): reads `/opt/clip-pipeline/creators.json`,
  outputs one row per handle in `creators[]`.
- Execute Command (named "probe newest", Loop Over Items over the handles):
  `python3 /opt/clip-pipeline/run_pipeline.py --creator {{ handle }} --max-videos 1 --no-cut`
  → Code node "dedupe": parses stdout JSON (stdout only — stderr is noise), takes
  `videos[0].video_id`. If it's already in `/opt/clip-pipeline/processed.txt`
  (one id per line, read it), stop for that item. If new: run the full cut
  (second Execute Command): `python3 /opt/clip-pipeline/run_pipeline.py --creator {{ handle }} --max-videos 1`,
  parse stdout JSON, then append the video_id to processed.txt.
- Final Code node: builds a Discord message per new episode:
  `🎬 New episode: <title> (<channel>) — <clips_found> beats cut` + a numbered
  list, one line per clip: `N. [start_ts-end_ts] peak` (from the `clips[]` array)
  + the `result_file` path at the end.
- Rules: always check `ok` and `errors[]` in the JSON. Never call yt-dlp for
  subtitles. Never edit the python scripts.

Build it, then show me the workflow for review before I activate it.

---

## PART 3 — Workflow: viral discovery

Now build workflow "Clip pipeline — viral discovery" (Manual Trigger for now):
Manual Trigger → Code "load queries" → Execute Command (Loop Over Items) →
LLM node → Code "format picks" → Discord Webhook.

- "load queries": reads `search_queries[]` from `/opt/clip-pipeline/creators.json`.
- Execute Command: `python3 /opt/clip-pipeline/run_pipeline.py --search "{{ query }}" --max-videos 10`
  — this only LISTS candidates (video_id, view_count, title). No downloads.
- LLM node: give it the combined candidate list with this instruction: "You are
  curating source videos for a finance reaction channel (reactions to couples'
  financial disasters, think Financial Audit). Pick the top 3 videos worth
  reacting to. For each: title, video_id, one-line reason. Skip anything that is
  not personal finance (city councils, corporate audits, clip-compilation channels)."
  Output as JSON array.
- "format picks": formats the 3 picks as a Discord message with full YouTube URLs
  (`https://youtube.com/watch?v=<id>`) and ends with: "Reply with a video_id and I'll cut the clips."

Build it, run it once with me watching, and show me the picks it makes.

---

## PART 4 — Workflow: manual cut + final checks

Now build workflow "Clip pipeline — manual cut":
Webhook Trigger (POST, body JSON: `{"video_id": "..."}` or `{"url": "..."}`) →
Code node (extract the 11-char video id from either field) → Execute Command:
`python3 /opt/clip-pipeline/run_pipeline.py --url {{ id }}` → Code (format the
clip list exactly like PART 2's format: `N. [start_ts-end_ts] peak` lines +
result_file path) → Discord Webhook.

Then, final checks — run these and report:
1. Manual cut with `_WBuNnEUJ9Y` → clip list arrives in Discord (should be ~21 beats).
2. Watch-list workflow twice in a row → 2nd run must be a no-op (dedupe works).
3. Show me one sample Discord message from each workflow.

When all three pass, we're done — tell me which workflows need activation.
