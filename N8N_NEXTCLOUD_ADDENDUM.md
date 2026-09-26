# n8n Addendum — Nextcloud Upload (clip-library staging)

> Paste everything below (starting at "You are adding…") into the n8n setup
> assistant. It applies ON TOP of the workflows built from
> `N8N_HANDOVER_PROMPT.md` (WF 1 watch-list, WF 2 discovery, WF 3 manual cut).

---

You are adding a Nextcloud upload step to the existing clip-pipeline workflows.
After clips are cut, upload the whole batch to the owner's Nextcloud so the
clip library accumulates in one place.

**Before wiring anything:** create an n8n credential:
- Credential type: **Nextcloud** (if this n8n version has no Nextcloud node,
  create a **Basic Auth** credential with the same user/password and use the
  HTTP Request fallback below)
- Base URL: `http://192.168.1.71/remote.php/dav/files/Nelson`
- User: `nelsonbrumaire@gmail.com`
- Password: ask the owner for the Nextcloud **app password** (not the account
  password; it looks like `XXXXX-XXXXX-XXXXX-XXXXX-XXXXX-XXXXX`)

## Target layout (STAGING only)

```
finance-channel/
└── _inbox/
    └── <video_id>/
        ├── source.json
        ├── clips.json
        └── <clip files>.mp4     (exactly the filenames the pipeline produced)
```

`_inbox/` is a staging area. A separate curation step (outside n8n) later moves
clips into per-idea folders (e.g. `finance-channel/001_financial-freefall/`)
and writes an `IDEA.md` per idea. Do **NOT** attempt to organize by video idea
in any workflow, and do not delete anything in `_inbox`.

## Verified WebDAV behavior on this Nextcloud (do not "improve")

- PUT to a path whose parent folder does not exist → **404**. Folders are NOT
  created automatically.
- MKCOL on a new folder → **201**. MKCOL on an existing folder → **405**
  (treat 405 as success = "already exists").
- PUT into an existing folder → **201**. DELETE → 204.

So before uploading a batch: MKCOL `finance-channel/_inbox` (accept 201/405),
then MKCOL `finance-channel/_inbox/<video_id>` (accept 201/405), then PUT files.

## Nodes to add (for every workflow that cuts clips: WF 1 and WF 3)

Insert AFTER the pipeline Execute Command has succeeded (`ok==true` and
`clips_cut > 0`) and BEFORE the Discord notification:

1. **Ensure folder** — HTTP Request node (or Code with fetch), using the
   Nextcloud credential:
   - `MKCOL http://192.168.1.71/remote.php/dav/files/Nelson/finance-channel/_inbox`
   - `MKCOL http://192.168.1.71/remote.php/dav/files/Nelson/finance-channel/_inbox/<video_id>`
   - Accept status 201 OR 405 from each; any other status → fail the execution
     with the status in the error message.
2. **Code node "build source.json"** — one item:
   ```json
   {
     "video_id": "...", "title": "...", "channel": "...", "url": "...",
     "cut_at": "<ISO timestamp>",
     "clips": [{"n": 1, "file": "clip01_HH-MM-HH-MM_slug.mp4",
                "start_ts": "02:20", "end_ts": "02:50", "peak": "..."}]
   }
   ```
   (fields from the parsed pipeline stdout JSON; `clips` = its `clips[]`).
3. **Upload loop** — for each file in
   `[source.json (built in step 2), clips.json (sibling of result_file),
   every clip mp4 (result_file dir + each clip `file` field)]`:
   - Read/Write Files from Disk (operation: Read) → binary item, then
   - Nextcloud node → resource **WebDAV** → operation **Upload** →
     Path `/finance-channel/_inbox/<video_id>/<filename>`
   - **Fallback (no Nextcloud node):** HTTP Request → method **PUT** to
     `http://192.168.1.71/remote.php/dav/files/Nelson/finance-channel/_inbox/<video_id>/<filename>`
     with the Basic Auth credential, body = the binary file.
   - Accept 201/204 per file; anything else → fail with filename + status.
4. **Notification** — append one line to the existing Discord message:
   `📁 uploaded: finance-channel/_inbox/<video_id>/ (N clips) — ping me to assemble into an idea folder`

## WF 2 (discovery) — no change

Discovery only lists candidates. Clips are cut (and uploaded) only when the
user triggers the webhook cut, which runs the WF 3 path including the upload.

## Hard rules (learned from testing — do not "improve" these)

- Upload only on a successful cut. On pipeline failure upload nothing and
  report the error as usual.
- Keep pipeline output filenames EXACTLY (`clipNN_HH-MM-HH-MM_slug.mp4`) —
  the downstream assembly step maps filenames to transcript beats by the
  embedded timestamps.
- Re-running the same video_id overwrites its files — intended, not a bug.
- Clips are ~1–3 MB each (480p, 30 s); no chunking needed.
- Nextcloud is LAN-only (192.168.1.71). If the n8n container cannot reach it,
  REPORT the connectivity problem — never expose Nextcloud publicly to fix it.
- Never write the app password into a node or environment variable —
  credential reference only.

## Verify (in order, before declaring done)

1. WF 3 with a known URL (e.g. `_WBuNnEUJ9Y`) → Discord clip list arrives AND
   the Nextcloud UI shows `finance-channel/_inbox/_WBuNnEUJ9Y/` containing
   `source.json`, `clips.json`, and every clip mp4. Open one mp4 in the UI and
   confirm it plays.
2. Run WF 3 again with the same URL → same files replaced, no duplicates, no
   stray folders.
3. WF 1 (manual trigger or daily) on a new episode → its
   `_inbox/<video_id>/` folder appears with all clips.
4. Show the owner the updated Discord notification + a screenshot of the
   Nextcloud folder for approval.
