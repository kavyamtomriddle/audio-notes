# Audio Notes Platform — Master Context & State

> **RULES FOR THE AI AGENT (read first, every session)**
> 1. Read this ENTIRE file before doing anything. It is the single source of truth. Your own memory is not.
> 2. Do only the phase the human tells you to do. Do not start the next phase.
> 3. If you make or discover anything that changes this file's facts (schema, endpoint, API behaviour, env var, decision, bug), UPDATE this file in the same task. Add a line to **§15 Progress Log**.
> 4. Never put secrets in code, logs, fixtures, tests or commits. Secrets live only in `.env` (git-ignored) and hosting dashboards. Never print env values.
> 5. Do not add dependencies or infrastructure outside §2. If you think one is needed, STOP and ask the human, with the reason.
> 6. Do not "improve" the architecture. If you believe something here is wrong, say so and ask before deviating.
> 7. The human will be questioned on this code in an interview. Keep code simple, small, commented where non-obvious. End every phase with a ≤15-line plain-English explanation of what you built and why, and list any deviations.
> 8. Before saying a phase is done: run the tests/checks listed for that phase and report real output.

---

## 1. Project (from the task brief)
Web platform: user uploads an audio file → Gnani ASR transcribes → transcript displayed → LLM summary displayed → past uploads listed and reopenable.

Brief requirements that drive design:
- Any length/size, must comfortably handle 2+ minute audio (we are bounded by the 50 MB storage cap; state this honestly).
- Next.js frontend, FastAPI backend, Postgres, storage bucket, background jobs.
- **Deployed at a public URL**, usable with zero setup.
- **`/architecture` page inside the app** (flow, where files live, long-audio handling, sync vs background, what we'd do with more time) **linking the GitHub repo**. Prose is written by the HUMAN; the agent only scaffolds it.
- **Failure must be visible** (upload fails, API timeouts, corrupt files). User always knows what is happening.
- **Progress must be visible** for long files.
- Deadline: Saturday 23:59 IST.

## 2. Tech stack — STRICT
| Layer | Choice |
|---|---|
| Frontend | Next.js (App Router) + TypeScript + Tailwind. Deployed on **Vercel**. |
| Backend | **FastAPI** (Python 3.11+), SQLAlchemy 2.x **async** + `asyncpg`, Alembic, `httpx` (async). Deployed on **Render** (native Python web service, NO Docker). |
| DB | Postgres on **Supabase**. Connect via the Supabase **session pooler** URI (see §11). |
| Storage | **Supabase Storage**, private bucket (default name `private_AUDIO`, set by env `SUPABASE_BUCKET`), **50 MB per-file limit**. |
| STT | **Gnani Batch STT** (`https://api.vachana.ai`), source type `cloud_storage` with a Supabase **signed download URL**. |
| LLM | Gemini (model name from env `LLM_MODEL`; do not hardcode, verify current model id in docs) for summaries. |
| Jobs | Postgres queue using `SELECT … FOR UPDATE SKIP LOCKED`, run as an `asyncio` task started in the FastAPI lifespan. |

**Forbidden:** Celery, Redis, RabbitMQ, ffmpeg/ffprobe, pydub, any local audio download/processing, Docker/docker-compose, webhooks, WebSockets, auth providers, ORMs other than SQLAlchemy, `supabase-py` (we call Storage REST directly with `httpx` — 2 endpoints, easier to explain), localStorage-based data (only the anonymous session id may use it).

## 3. Gnani Batch STT — VERIFIED FACTS (from live curl tests, Oct 2026)
Base URL `https://api.vachana.ai`. Auth header on every call: `X-API-Key-ID: <GNANI_API_KEY>`.
Docs (fetch only if you need more, don't crawl):
- https://docs.gnani.ai/api/STT/speech-to-text.md
- https://docs.gnani.ai/api/STTBatch/Introduction.md · Create_Job.md · Start_Job.md · Get_Job.md · List_Jobs.md · Get_Job_Files.md · Cancel_Job.md

**Flow (all verified):**
1. `POST /stt/v3/batch/jobs` with `Content-Type: application/json` body:
   ```json
   {"config":{"model":"gnani-prisma-v2.5","language_code":"en-IN","mode":"transcribe"},
    "source":{"type":"cloud_storage","auth":{"mode":"public"},"paths":["<SIGNED_DOWNLOAD_URL>"]}}
   ```
   → `{"job_id":"…","status":"CREATED","total_files_accepted":1,"created_at":"…","message":"…"}`. Creating does NOT start work. URL is validated at Start, not Create.
2. `POST /stt/v3/batch/jobs/{job_id}/start` → `{"job_id","status":"STARTING","message":"Job start accepted…"}`. Docs list a **409** here → treat as "already started".
3. `GET /stt/v3/batch/jobs/{job_id}` → `status` seen: `CREATED`, `STARTING`, `IN_PROGRESS`, `COMPLETED`. Documented lifecycle: CREATED → STARTING → QUEUED → IN_PROGRESS → COMPLETED (QUEUED is documented but not yet observed in our tests; treat it as non-terminal). Documented terminal: `COMPLETED`, `PARTIAL_FAILURE`, `FAILED`, `START_FAILED`, `CANCELLED`. `START_FAILED` arrives ~2 s after Start when Gnani cannot fetch the URL; `cancel_reason` has details and `progress.percent` is meaningless here. Also has `progress{total_files,completed_files,failed_files,…,percent}`, `started_at`, `completed_at`. **`progress.percent` only jumps 0→100; DO NOT use it for UI progress.** Min poll interval 10 s.
4. `GET /stt/v3/batch/jobs/{job_id}/files` → `{"data":[{"file_id","status","duration_seconds","error_message","transcript_url",…}]}`. **`duration_seconds` is a STRING here** (`"92.29"`). Filter `?status=COMPLETED` works. **This endpoint returns `429 RATE_LIMITED` aggressively** (even minutes later for one job).
5. `GET <transcript_url>` (follow redirects, **no API key needed**, **expires in 1 hour → fetch immediately and store**). JSON has `full_transcript` (string) and `segments` (coarse; `speaker_id` is noise even with diarization off; `duration`-like fields here are numbers). **Use ONLY `full_transcript`.**

**Measured:** 92.29 s audio → ~9–12 s server-side; wall time ≈ 10–21 s with 10 s polling (≈ 0.13× audio duration; treat `0.13` as a tunable constant `EST_RATIO`).
**Private Supabase bucket + signed URL (`…/storage/v1/object/sign/<bucket>/<path>?token=…`) works** with `auth.mode = "public"`. Generate the signed URL in the worker at job-creation time with ~3 h expiry (Gnani fetches after Start; 30 min download limit). Never a 7-day URL.
**Transcript quality:** lowercase, no punctuation, ASR mishears (e.g. "gift" for GIF). The summary prompt must say so.
**Silent / music-only audio:** fails with an "Empty transcript after 3 retries"-style file error → map to `NO_SPEECH_DETECTED`. Also treat an empty/whitespace `full_transcript` the same way.
**Language:** no auto-detect. UI has a language selector, default `en-IN`; take the supported code list from Create_Job.md. Language codes live in ONE backend constant (`app/constants.py`), used by both validation and `/api/config`.
**Unverified (treat defensively, log real values once seen):** whether `Retry-After` is sent on 429; exact file-level failure statuses/messages; behaviour for non-English audio; exact supported audio formats (read Create_Job.md and encode the allow-list in one constant).

### Error shapes (Gnani returns at least 4) — one function `normalize_gnani_error(resp) -> GnaniError(code, message, http_status, retryable)`
1. Plain text body, e.g. `Internal Server Error` (500).
2. `{"detail":{"error_code":"RATE_LIMITED","message":"…","status_code":429}}`
3. `{"error":"CODE","message":"…"}`
4. `{"success":false,"error":{"type":"…","message":"…"}}` (REST-style)
Rules: 429/500/502/503/504/timeouts → retryable. 401/403 → `PROVIDER_AUTH` non-retryable (bad key / out of credits) with a clear message. Never crash on an unknown shape; fall back to status code + truncated text.

## 4. End-to-end flow
1. **Browser** generates/reads anonymous `session_id` (UUID in localStorage). Reads audio duration from file metadata (`HTMLAudioElement`, hint only, may be missing).
2. `POST /api/jobs/initiate` → backend validates (size ≤ 50 MB, extension allow-list, caps §12), inserts row `awaiting_upload`, asks Supabase Storage for a **signed upload URL** for path `{session_id}/{job_id}/{safe_filename}`, returns it.
3. Browser **PUTs the file directly to Supabase with `XMLHttpRequest`** (real byte progress; bypasses Vercel/Render payload limits). This is the ONLY direct browser→Supabase call; everything else goes through our API.
4. `POST /api/jobs/{id}/complete` → backend verifies the object exists in storage (size > 0), sets `status='queued'`.
5. **Worker** (asyncio task in the FastAPI process) advances jobs one small step at a time (§8): create+start Gnani job → poll → fetch transcript → summarize.
6. Browser polls **our** `GET /api/jobs/{id}` every 3 s (never Gnani) and renders stage, elapsed time, estimated progress bar, transcript, summary, errors, retry buttons.
7. After the transcript is safely stored, the audio object is deleted from storage (saves the 1 GB free quota, privacy). Decided: yes.

## 5. Database schema (Alembic migration must match EXACTLY)
Table `uploads` (use TEXT + CHECK constraints, not PG enums, so migrations stay trivial):
```sql
id               uuid primary key default gen_random_uuid(),
session_id       text not null,
filename         text not null,
size_bytes       bigint not null,
content_type     text,
language_code    text not null default 'en-IN',
storage_path     text not null unique,
duration_hint_s  double precision,           -- browser metadata hint, UNTRUSTED, estimate only
status           text not null default 'awaiting_upload'
                 check (status in ('awaiting_upload','queued','transcribing','summarizing','completed','failed')),
summary_status   text not null default 'pending'
                 check (summary_status in ('pending','done','failed')),
gnani_job_id     text,                       -- saved IMMEDIATELY after Create (idempotency)
gnani_started    boolean not null default false,
gnani_status     text,                       -- last seen Gnani job status (for stage text)
files_attempts   int not null default 0,     -- consecutive 429/failed /files fetches
transcript       text,
summary          text,
error_code       text, error_message text, retryable boolean,
summary_error    text,
attempts         int not null default 0,     -- worker claims; guards crash loops
lease_expires_at timestamptz,                -- NULL = not held
next_run_at      timestamptz not null default now(),
created_at       timestamptz not null default now(),
queued_at        timestamptz, processing_started_at timestamptz, completed_at timestamptz,
updated_at       timestamptz not null default now()
```
Indexes: `(session_id, created_at desc)`, `(status, next_run_at)`.

## 6. State machine
```
awaiting_upload ─/complete→ queued ─worker→ transcribing ─transcript stored→ summarizing ─→ completed
      │                        │                 │                                  │
      └─sweeper/abandon→ failed ◄────────────────┴── provider/validation errors     └─LLM fails→ completed + summary_status='failed'
```
- A **summary failure is NOT a failed upload**: `status='completed'`, `summary_status='failed'`, transcript intact; UI shows transcript + "Retry summary". ("Partial" is a UI rule derived from these two fields, not a stored state.)
- `failed` always carries `error_code`, `error_message` (human-friendly), `retryable`.
- Retry of a failed job: keep `gnani_job_id` if the Gnani job did not itself fail (so we poll/fetch it again at no cost); if Gnani's job failed, clear `gnani_job_id`/`gnani_started` and requeue.

## 7. API (FastAPI, all under `/api`, JSON; every request except `/health` and `/api/config` needs header `X-Session-Id`)
| Method & path | Purpose |
|---|---|
| `GET /health` | `{ok:true}` (also used by the frontend to warm a sleeping Render instance) |
| `GET /api/config` | Static settings, no session header, no secrets, no DB access. Returns `{"languages":[{"code":"en-IN","label":"English (India)"},…],"extensions":["mp3",…],"max_upload_bytes":52428800,"max_duration_hint_s":7200,"est_ratio":0.13}` (example abbreviated: the real lists are the FULL lists from Gnani Create_Job.md). Values come from the backend's constants and env vars. |
| `POST /api/jobs/initiate` | body `{filename,size_bytes,content_type,duration_hint_s?,language_code}` → `{id, upload_url, expires_in}`; 413/415/429 with friendly `error_code` |
| `POST /api/jobs/{id}/complete` | verify object exists → `queued`; 409 if state wrong |
| `GET /api/jobs/{id}` | full job: ids, filename, status, gnani_status, summary_status, transcript, summary, error_*, summary_error, duration_hint_s, timestamps |
| `GET /api/jobs` | this session's jobs, newest first, WITHOUT transcript/summary bodies (include a 140-char summary snippet: first 140 chars of summary, or null if no summary yet) |
| `POST /api/jobs/{id}/retry` | only if `failed` and `retryable` |
| `POST /api/jobs/{id}/retry-summary` | only if `transcript` present and `summary_status='failed'` |
Job access is scoped by `session_id` match (404 otherwise). Job ids are random UUIDs. This is **not real auth**; say so honestly on `/architecture`.
Error body everywhere: `{"error_code":"…","message":"friendly text","retryable":bool}`.

## 8. Worker (the part the human must be able to explain)
Single asyncio task per process, started in FastAPI `lifespan`, stopped on shutdown. Env `ENABLE_WORKER=true` on Render. DB access must be async (no sync calls that block the event loop).

**Loop:** `claim → run ONE step → write result + schedule next → release lease`. If nothing claimable, `await asyncio.sleep(2)`.

**Claim (atomic):**
```sql
SELECT id FROM uploads
WHERE status IN ('queued','transcribing','summarizing')
  AND next_run_at <= now()
  AND (lease_expires_at IS NULL OR lease_expires_at < now())   -- NULL check is REQUIRED: NULL < now() is NULL, never true
ORDER BY next_run_at
FOR UPDATE SKIP LOCKED
LIMIT 1;
-- same transaction: UPDATE … SET lease_expires_at = now() + interval '120 seconds', attempts = attempts + 1
```
Lease is held only while a step runs (lease 120 s covers the longest step, the LLM call). A crashed process leaves an expired lease, so another claim picks the job up. `attempts` counts consecutive claims WITHOUT a completed step: reset to 0 after any step that finishes without error, and on manual retry. If it exceeds `MAX_CLAIMS` (default 5) → fail `WORKER_STUCK`, retryable. (A long file legitimately needs dozens of claims; only crash loops should trip this.)
Three separate guards, not a conflict: `attempts` (crash loops only, MAX_CLAIMS), `files_attempts` (/files failures, cap 8 → PROVIDER_RATE_LIMITED), and TRANSCRIBE_TIMEOUT_S + the sweeper (a Gnani job stuck IN_PROGRESS). A handled 429 resets `attempts` but increments `files_attempts`.

**Steps by status:**
- `queued`: if no `gnani_job_id`: build signed download URL (3 h) → `POST create` → **commit `gnani_job_id` immediately**. If not `gnani_started`: `POST start` (409 ⇒ conflict (running or already failed), call `GET job` to determine state) → commit `gnani_started=true`, `status='transcribing'`, `processing_started_at=now()`, `next_run_at=now()+10s`.
- `transcribing`: `GET job`; save `gnani_status`.
  - `IN_PROGRESS/STARTING/QUEUED` → `next_run_at = now()+10s`.
  - `COMPLETED` → `GET /files`. On 429 (or retryable error): `files_attempts += 1`, `next_run_at = now() + min(2**files_attempts, 30)s` (honour `Retry-After` if present); do NOT sleep inside the loop; after 8 attempts → fail `PROVIDER_RATE_LIMITED` (retryable, keeps `gnani_job_id`). On success: take `data[0]`, check file status/`error_message`, `GET transcript_url` immediately, read `full_transcript`. Empty ⇒ fail `NO_SPEECH_DETECTED`. Else store transcript, `status='summarizing'`, `summary_status='pending'`, `next_run_at=now()`, delete audio object (best-effort).
  - `FAILED/PARTIAL_FAILURE/CANCELLED` → best-effort `GET /files` for `error_message`, map via §9, fail.
  - `START_FAILED` (e.g. invalid URL) → fail the Gnani job, clear `gnani_job_id` and `gnani_started`, mark retryable. This ensures a manual retry builds a fresh signed URL and a new Gnani job.
  - Gnani job stuck > `TRANSCRIBE_TIMEOUT_S` (default 3600) → fail `PROVIDER_TIMEOUT` (retryable).
- `summarizing`: LLM call (§10). Success → `summary`, `summary_status='done'`, `status='completed'`, `completed_at`. Failure (after 2 quick retries) → `summary_status='failed'`, `summary_error`, `status='completed'`.

**Global Gnani pacing:** all Gnani calls go through one client with an `asyncio.Lock` and ≥ 1 s spacing (all users share one API key; limits are tight). Every Gnani call has a timeout (e.g. 30 s) and errors pass through `normalize_gnani_error`.

**Sweeper** (same loop, every 5 min): `awaiting_upload` older than 30 min → `failed UPLOAD_ABANDONED` (delete object best-effort); `queued/transcribing` older than 2 h → `failed PROVIDER_TIMEOUT`.

**Known unclosable window (document on /architecture):** if the process dies after Gnani returns a job id but before our commit, one billed orphan job exists. Cost is one file; accepted.
**Known limitation (document):** on Render free, the instance sleeps when idle; the worker only runs while the instance is awake. The frontend's 3 s polling keeps it awake while a tab is open; if the tab closes mid-job, the job resumes (via lease expiry / `next_run_at`) the next time the service wakes. A repeating step bug retries every 30 s until the sweeper's 2 h limit.

## 9. Error codes (stable strings; UI maps each to friendly text + whether to show Retry)
`FILE_TOO_LARGE`(413) · `UNSUPPORTED_FORMAT`(415) · `RATE_LIMITED_SESSION`(429) · `DAILY_CAP_REACHED`(429) · `UPLOAD_INCOMPLETE`(409, object missing/empty at /complete) · `UPLOAD_ABANDONED` · `NO_SPEECH_DETECTED` · `CORRUPT_OR_UNREADABLE_AUDIO` (Gnani file-level failure/SKIPPED) · `PROVIDER_AUTH` (non-retryable) · `PROVIDER_RATE_LIMITED`(retryable) · `PROVIDER_ERROR`(retryable) · `PROVIDER_TIMEOUT`(retryable) · `WORKER_STUCK`(retryable) · `SUMMARY_FAILED` (stored only in `summary_error`, not as job failure).

## 10. LLM summary
Input = stored `transcript`. System/prompt must say: *"The input is raw ASR output: lowercase, no punctuation, may contain misheard words or names. Do not invent facts. Produce: 1) a 2–3 sentence TL;DR, 2) key points as bullets, 3) action items / decisions if any (omit the section if none), 4) topics discussed."* Output Markdown. We **display the raw transcript as stored** (label it "raw ASR output"); we do not store an LLM-rewritten transcript.
Long transcripts: if `len(transcript) > SUMMARY_CHUNK_CHARS*2` (default chunk 30 000 chars) → summarize chunks (map) then merge (reduce). Per-call timeout; 2 retries with backoff on 429/5xx. LLM key is backend-only.

## 11. Environment variables (ONE `.env` at the repo root; `.env.example` committed with empty values)
`DATABASE_URL` (Supabase **session pooler** URI, converted to `postgresql+asyncpg://`; the direct DB host is IPv6-only and may not be reachable from Render — if a transaction pooler (port 6543) is ever used, set asyncpg `statement_cache_size=0`) · `SUPABASE_URL` · `SUPABASE_SERVICE_KEY` (service-role/secret key, backend ONLY) · `SUPABASE_BUCKET` · `GNANI_API_KEY` · `GNANI_BASE_URL=https://api.vachana.ai` · `LLM_API_KEY` · `LLM_MODEL` · `CORS_ORIGINS` (comma list: Vercel URL + `http://localhost:3000`) · `MAX_UPLOAD_BYTES=52428800` · `MAX_DURATION_HINT_S=7200` · `JOBS_PER_SESSION_PER_DAY=10` · `JOBS_GLOBAL_PER_DAY=100` · `EST_RATIO=0.13` (served to the frontend via `/api/config`) · `TRANSCRIBE_TIMEOUT_S=3600` · `MAX_CLAIMS=5` · `SUMMARY_CHUNK_CHARS=30000` · `ENABLE_WORKER=true`.
Frontend: `NEXT_PUBLIC_API_URL` only. No secrets in the frontend, ever.
Storage REST (call with `httpx`, `Authorization: Bearer <service key>` + `apikey` header; verify exact paths/response fields against Supabase Storage docs and a smoke test): create signed upload URL (`POST /storage/v1/object/upload/sign/{bucket}/{path}`), create signed download URL (`POST /storage/v1/object/sign/{bucket}/{path}` body `{"expiresIn":10800}`; the returned `signedURL` is relative — prefix `{SUPABASE_URL}/storage/v1`), check object exists, delete object.

## 12. Limits, abuse, validation (no real auth, public URL, shared ₹1,000 credits)
- Size ≤ 50 MB enforced in browser, in `/initiate` (declared size), by the bucket's file-size limit, and re-checked at `/complete`. Extension allow-list (from Gnani docs). Reject early with friendly messages; for oversize suggest compressing (e.g. mp3 ≈ 1 MB/min at 128 kbps; WAV is far bigger). Language codes and extensions live in ONE backend constant (`app/constants.py`), used by both validation and `/api/config`.
- Per-session and global daily job caps from env (count rows in DB; simple, explainable).
- Duration hint > `MAX_DURATION_HINT_S` → reject (hint is untrusted; real cap is bytes).
- "Corrupt file" has no local detection (no ffprobe): rely on Gnani's file-level failure → `CORRUPT_OR_UNREADABLE_AUDIO`.

## 13. Frontend spec (Next.js App Router, Tailwind)
- On load, fetch `/api/config` and use it for the language select, client-side size/format validation, and the progress estimate (`min(0.95, elapsed / (est_ratio * duration_hint_s))`). If it fails, show the "server is waking up" banner and retry with backoff; do not fall back to hardcoded values.
- `/` : language select (default en-IN), file dropzone, validation messages, upload progress bar (XHR `upload.onprogress`), then history list (from `GET /api/jobs`, status badges, click to open).
- `/jobs/[id]` : stage text + elapsed timer + estimated progress bar; transcript (labelled raw ASR output, copy/download .txt); summary (Markdown); error panel with friendly text + Retry button iff `retryable`; "Retry summary" when `summary_status='failed'`.
- Stage text mapping: `awaiting_upload` Uploading · `queued` Queued · `transcribing` + gnani_status (`STARTING/QUEUED` "Starting transcription", `IN_PROGRESS` "Transcribing", `COMPLETED` "Fetching transcript", while retrying files fetch "Waiting on speech provider") · `summarizing` Summarizing · `completed` Done.
- Progress bar while transcribing: `min(0.95, elapsed / (est_ratio * duration_hint_s))` where `est_ratio` comes from `/api/config` (if no hint, indeterminate bar). If elapsed > 2× estimate show "Taking longer than expected — still working". Elapsed is computed from server timestamp `processing_started_at`.
- Polling hook: every 3 s to OUR API; stop on terminal state (`failed`, or `completed` with summary not pending); on network error show a "reconnecting…" banner and keep trying with backoff.
- On first load call `/health`; if slow (>3 s) show "Server is waking up (free tier, up to ~1 min)".
- `/architecture` : scaffold only (headings + placeholder `TODO(human)` paragraphs + a "Facts to cover" list taken from this file + repo link). The human writes the prose. Include sections: flow upload→transcript, where files live, long audio handling (Gnani Batch via signed URL, 50 MB bound), sync vs background, failure handling, limits/abuse, known limitations (§8), alternatives considered (REST 30 s limit + chunking with ffmpeg; webhooks; Redis/Celery), what we'd do with more time.
- Accessibility basics, mobile-friendly, no UI libraries beyond Tailwind unless asked.
- **Corrections:** (a) the summary is shown as plain text (whitespace-pre-wrap), no markdown library; (b) API timestamps are parsed by ONE helper that treats a missing timezone as UTC and trims fractional seconds to 3 digits (Safari); (c) elapsed = max(0, now - processing_started_at); if duration_hint_s is missing or est_ratio unavailable the bar is indeterminate; (d) the job page is a client component using useParams; (e) /architecture is scaffolded in Phase 3 (F4); the human writes the prose.

## 14. Repo layout & deployment
```
/Context.md   /README.md   /.gitignore (.env, node_modules, __pycache__, .venv)   /.env.example
/backend  (app/main.py, app/config.py, app/constants.py, app/db.py, app/models.py, app/schemas.py, app/routes/jobs.py,
           app/services/{storage.py,gnani.py,llm.py}, app/worker.py (DB helpers only), app/sweeper.py, app/loop.py,
           app/steps/{deps.py,queued.py,transcribing.py,completed.py,summarizing.py,dispatch.py},
           alembic/, tests/, requirements.txt, render.yaml)
/frontend (Next.js app, vercel.json only if needed; approved files: src/lib/{types,session,api,upload,errors,validate,audio,stage,polling}.ts; src/hooks/{useConfig,useHealth,usePollJob}.ts; src/components/{HealthBanner,UploadForm,JobStatus,TranscriptPanel,SummaryPanel,ErrorPanel,HistoryList}.tsx; src/app/{page.tsx, jobs/[id]/page.tsx, architecture/page.tsx}; src/lib/tests/*.test.ts; vitest.config.ts)
/docs/fixtures (scrubbed Gnani responses: create, start, job, files, transcript, 429, empty-transcript)
```
Step functions live in `app/steps/`; `app/worker.py` keeps only the DB helpers.
Render: native Python runtime, build `pip install -r requirements.txt`, start `uvicorn app.main:app --host 0.0.0.0 --port $PORT`, health check `/health`, env vars set in dashboard. Vercel: root dir `frontend`, `NEXT_PUBLIC_API_URL` set in dashboard. Put Supabase and Render in the same/nearby region. Explicit CORS from `CORS_ORIGINS`.

### 15. State, progress log, handoff (AGENT KEEPS THIS SECTION CURRENT)
**Current phase:** Phase 3 - frontend (branch wip-phase-3)
**Deploy URLs:** Backend: https://audio-notes-n5b2.onrender.com · Frontend: https://audio-notes-red.vercel.app · render.yaml PYTHON_VERSION=3.13.3 (Render configured manually in dashboard; render.yaml is documentation only)
**Phase checklist:** [x] 0 Scaffold + hello-world deploy · [x] 1 DB/storage/upload API · [x] 2a Gnani client+LLM+fixtures+mocked tests (human-verified: 47 tests pass, live check reached COMPLETED, 202/409/START_FAILED handled, object_exists/delete_object use Storage LIST API) · [x] 2b-i · [x] 2b-ii-a · [x] 2b-ii-b · [x] 2b-ii-c · [x] 2b-ii-d · [x] 2b-iii-a · [x] 2b-iii-b · [x] 2b-iii-c · [x] 2b-iii-d · [x] Fix A–E · [x] F1a · [x] F1b · [x] F2a · [x] F1c, F2b, F2c, F3 · [ ] F4, F5 · [ ] 4 Hardening+/architecture scaffold+deploy config · [ ] 5 Human: prose, mock interview, submit
**Decisions made (human):** audio deleted after transcript stored: YES · LLM_MODEL: gemini-3.8-flash · Render region: Singapore
**Deviations from this file:** 2b-ii-b created app/steps/__init__.py (not in allowed-files list but required for Python package imports)
**Known issues / next steps:** Phase 2 complete and merged (tag phase-2); real API fixtures live in docs/fixtures/api_*.json. Next: Phase 3 Frontend. deps.py provides transactional_session (commit/rollback) and Deps dataclass; steps use deps.session and deps.gnani_client; commits are owned by transactional_session in the loop, not by individual steps. queued.py conforms to the same import style as transcribing.py (imports worker helpers and gnani directly at module level). Next.js pinned at 15.1.0 (has a deprecation warning about a CVE; update if desired before deploy). ESLint 9.39.5 deprecated warning (non-blocking). User's .env has JOBS_GLOBAL_PER_DAY=2 (tests override this to 100 via conftest). Unit tests run on SQLite with fakes — they never exercise Postgres-only SQL or the real Storage/Gnani APIs. Integration tests (19) use a throwaway Postgres schema and exercise the real claim/lease/step SQL; excluded by default via pyproject.toml addopts = -m "not integration". Config uses plain module-level variables (not pydantic-settings); worker.py imports MAX_CLAIMS directly. sweeper.sweep() calls session.commit() internally; the transactional_session in the sweep closure handles this (SQLAlchemy's session.begin() context is a no-op on already-committed transactions). Lifespan reads config.ENABLE_WORKER via `from app import config` (module reference, not value copy) so monkeypatching works in tests. conftest.py's ASGITransport does NOT run the lifespan; no existing test enters it. 2b-i facts: pyproject sets asyncio_default_fixture_loop_scope = "module"; integration tests use NullPool and statement_cache_size=0; integration tests use DB-side now() (Python clocks drifted from the DB); the updated_at integration assertions only allow a 2 s tolerance, so they are weak (strengthen later, low priority).
**Progress log (newest first, one line each: date · what · files touched · tests run):**
- 2026-10-03 · Phase F3: HistoryList component, layout.tsx header, page.tsx updated. · frontend/src/components/HistoryList.tsx, frontend/src/app/layout.tsx, frontend/src/app/page.tsx · lint ✔ tsc ✔ test ✔ build ✔
- 2026-10-03 · Phase F2b: JobStatus, TranscriptPanel, SummaryPanel, ErrorPanel presentational components. · frontend/src/components/{JobStatus,TranscriptPanel,SummaryPanel,ErrorPanel}.tsx · lint ✔ tsc ✔ test ✔ build ✔
- 2026-10-03 · Phase F2a: stage.ts pure helpers (timezone normalisation, status mapping), vitest config, test fixtures. 34 vitest tests pass. Next.js build passes. · frontend/src/lib/stage.ts, frontend/src/lib/__tests__/*.ts, frontend/vitest.config.ts, frontend/package.json · lint ✔ tsc ✔ test ✔ build ✔
- 2026-10-03 · Phase F1b: errors.ts (friendly mappings for §9), useConfig.ts (mount fetch + backoff retry), useHealth.ts, HealthBanner.tsx (accessibility, status UI). · frontend/src/lib/errors.ts, frontend/src/hooks/{useConfig,useHealth}.ts, frontend/src/components/HealthBanner.tsx · lint ✔ tsc ✔ build ✔
- 2026-10-03 · Phase F1a: types.ts (backend mirror), session.ts (SSR-safe UUID+localStorage), api.ts (ApiError class, request<T>, typed endpoints, detail-wrapped error parsing), upload.ts (XHR PUT matching smoke_upload.py). · frontend/src/lib/{types,session,api,upload}.ts · lint ✔ tsc ✔ build ✔
- 2026-10-03 · Updated Context.md to prep Phase 3 (Frontend guardrails, §13 corrections, §14 layout, reset checklist, recorded Phase 2 complete). · Context.md · n/a
- 2026-10-03 · Phase 2 verified live: Gnani reports file errors via FAILED job with messages like "no speech detected in Ns of audio" and "ffprobe could not read the file", mapped by classify_file_error; PROVIDER_AUTH and summary_failed paths verified live; retry, retry-summary and crash recovery results verified live. · Context.md · n/a
- 2026-10-03 · Fix E: unified error classification in `classify_file_error` for transcribing/completed. Added 5 tests for error classification. · backend/app/steps/{errors,transcribing,completed}.py, backend/tests/steps/test_errors.py, Context.md · 160 unit pass
- 2026-10-03 · Fix D: build_default_worker creates one shared httpx.AsyncClient(timeout=60), passes as both gnani_client and llm_client (was passing the gnani module + None). Lifespan closes client in finally. 5 new tests in test_worker_wiring.py (real service calls via MockTransport, type check, is_closed, no secrets, AST static check). Updated test_loop.py lifespan test for new "client" key. · backend/app/{loop,main}.py, backend/tests/{test_worker_wiring,test_loop}.py, Context.md · 155 unit pass
- 2026-10-03 · Fix C: narrowed except in queued.py to httpx.RequestError and RuntimeError. Replaced _log_unexpected with log_reschedule at WARNING with traceback frames and redacted exc details. Added WARNING caplog test. · backend/app/steps/{queued,dispatch}.py, backend/tests/test_steps_dispatch.py, Context.md · 150 unit pass
- 2026-10-03 · Fix A: claim_job SELECT * after UPDATE (full row returned, no more KeyError on job["storage_path"] etc.); docstring updated. New tests/test_job_contract.py (1 unit, regex-validates all job[x] keys vs Upload columns). Appended test_claim_returns_all_columns to integration/test_claim.py (19 integration total). · backend/app/worker.py, backend/tests/test_job_contract.py, backend/tests/integration/test_claim.py, Context.md · 149 unit pass, 19 integration deselected
- 2026-10-03 · Phase 2b-iii-d: e2e_worker.py script for end-to-end testing of full flow (upload, wait, verify) matching smoke_upload.py cleanup logic. · backend/scripts/e2e_worker.py, Context.md · no tests
- 2026-10-03 · Phase 2b-iii-c: loop.py (LoopState, _idle, run_worker, shutdown_worker, build_default_worker closures with transactional_session), lifespan in main.py (ENABLE_WORKER gate, one task, shutdown), 18 unit tests. · backend/app/loop.py, backend/app/main.py, backend/tests/test_loop.py, Context.md · 148 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-ii-a: deps.py (transactional_session + Deps dataclass + make_deps), queued.py (create→start Gnani job, 409 conflict handling, error routing), 13 unit tests. · backend/app/steps/{deps,queued}.py, backend/tests/test_steps_queued.py, Context.md · 130 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-iii-b: POST /retry and POST /retry-summary, 11 tests. · backend/app/routes/jobs.py, backend/tests/test_retry_routes.py, Context.md · 117 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-iii-a: background sweeper (abandoned uploads, timed-out gnani/summarizing), _aware helper for SQLite naive datetimes, 12 unit tests. · backend/app/sweeper.py, backend/tests/test_sweeper.py, Context.md · 106 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-ii-d: step_summarizing (LLM call, success/failure→completed, redact()), dispatch.run_step (route queued/transcribing/summarizing, swallow exceptions 30 s, unknown status 60 s), 20 unit tests. · backend/app/steps/{summarizing,dispatch}.py, backend/tests/{test_steps_summarizing,test_steps_dispatch}.py, Context.md · 94 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-ii-c: handle_completed (get_files 429 back-off/cap-8, file status validation, transcript fetch, finish_step→summarizing, best-effort storage delete), 14 unit tests. · backend/app/steps/completed.py, backend/tests/test_steps_completed.py, Context.md · 74 unit pass, 18 integration deselected
- 2026-10-03 · Phase 2b-ii-b: step_transcribing (poll Gnani get_job, route by status, timeout check, provider error handling), completed.py stub (NotImplementedError), 13 unit tests. · backend/app/steps/{__init__,transcribing,completed}.py, backend/tests/test_steps_transcribing.py, Context.md · 60 unit pass, 18 integration deselected
- 2026-10-03 · Updated Context.md: added §17 guardrails, recorded 2b-i facts, replaced Phase 2b checklist, updated §14 repo layout for app/steps/. · Context.md · n/a
- 2026-10-02 · Fixed 9 failing integration tests due to clock skew/transaction time issues by converting Python datetime generation (for `_insert_job` and test calls) to database-side `text("now()")` expressions and relaxing `updated_at` assertions. · backend/tests/integration/test_claim.py · 47 unit pass, 18 integration collected
- 2026-10-02 · Fixed integration test setup: root cause was SET search_path auto-beginning a transaction then conn.begin() failing with InvalidRequestError; also NullPool, loop_scope="module", AUTOCOMMIT teardown, safe schema name validation, concurrent test rewritten with explicit conn management. · backend/tests/integration/test_claim.py, backend/pyproject.toml, Context.md · 47 unit pass (18 integration deselected)
- 2026-10-02 · Phase 2b-i: worker.py DB helpers (claim_job, finish_step, fail_job, release_lease) with raw SQL. SMOKE_SESSION_PREFIX in constants.py. pyproject.toml for pytest config (integration marker, addopts). 18 integration tests in tests/integration/test_claim.py using throwaway PG schema. · backend/app/{worker,constants}.py, backend/pyproject.toml, backend/tests/integration/{__init__,test_claim}.py, Context.md · 47 unit pass, 18 integration collected (deselected by default)
- 2026-10-02 · Phase 2a human-verified: 47 mocked tests pass; live check reached COMPLETED; 202/409/START_FAILED handled; object_exists/delete_object use Storage LIST API. START_FAILED arrives ~2 s after Start; on 409 call Get Job; smoke_upload.py cleans storage then DB. · Context.md · n/a
- 2026-10-02 · Checkpoint 4: Added START_FAILED fixture and parsing test. Updated Context.md facts on 409 and START_FAILED meaning. · docs/fixtures/gnani_get_job_start_failed.json, backend/tests/test_phase2a.py, Context.md · 27 tests pass
- 2026-10-02 · Checkpoint 2 & 3: Diagnosed and fixed object_exists false positives by rewriting it to use POST /storage/v1/object/list/{bucket}. Updated delete_object to verify the deleted object's name is in the response array. Added object_exists tests (present, absent, 404). Updated gnani_live_check.py to not delete audio if it fails before terminal status. · backend/app/services/storage.py, backend/tests/test_phase2a.py, backend/scripts/gnani_live_check.py · tests pass
- 2026-10-02 · Checkpoint 1: fixed gnani.py to treat all 2xx as success, updated start_job to treat 409 as CONFLICT needing get_job. Added gnani_409_conflict fixture and tests for 202/409. · backend/app/services/gnani.py, backend/tests/test_phase2a.py, docs/fixtures/gnani_409_conflict.json · tests pass
- 2026-10-02 · Phase 2a complete: gnani.py (paced async client, asyncio.Lock+1s spacing, normalize_gnani_error 4 shapes, defensive duration_seconds parsing, EmptyTranscriptError), llm.py (Gemini REST generateContent via httpx, ASR-aware prompt, chunked map-reduce for long transcripts), 7 scrubbed fixtures in docs/fixtures/, 21 mocked tests (storage URL prefix, delete success/failure, gnani error normalization ×6, empty transcript, duration parsing ×3, LLM chunking ×4). Fixed delete_object: uses client.request('DELETE') with json body, inspects response array, returns False on empty list, raises on HTTP error (no silent swallowing). Added signed-URL prefix test. smoke_upload.py cleanup moved to finally block. gnani_live_check.py script added. · backend/app/services/{gnani,llm}.py, backend/app/services/storage.py, backend/tests/test_phase2a.py, backend/scripts/{smoke_upload,gnani_live_check}.py, docs/fixtures/{gnani_create_job,gnani_start_job,gnani_get_job_completed,gnani_get_files,gnani_transcript,gnani_429_rate_limited,gnani_empty_transcript}.json · 41/41 tests pass ✓ (20 Phase 1 + 21 Phase 2a)
- 2026-10-02 · Phase 1 verified live by human: migration 0001 applied; 28 columns, CHECKs and indexes checked in Supabase; smoke script passed against real Supabase; signed upload URL accepts raw PUT; RLS enabled on uploads and alembic_version (manual SQL); merged to main, tagged phase-1, deployed to Render and verified. Hand fixes: alembic/env.py rewritten to async (asyncpg); /storage/v1 prefix added to both signed-URL builders in storage.py; local .env dev caps raised (local only) · Context.md · n/a
- 2026-10-02 · Phase 1 complete: config.py extended (all §11 vars), db.py (lazy async engine), models.py (Upload, SQLAlchemy 2.x), constants.py (languages/extensions/errors from Gnani docs), schemas.py, services/storage.py (httpx Supabase REST), routes/jobs.py (GET /api/config, POST initiate/complete, GET jobs/job), Alembic migration 0001, tests (20 pass), smoke_upload.py · backend/app/{config,constants,db,models,schemas}.py, backend/app/services/{__init__,storage}.py, backend/app/routes/{__init__,jobs}.py, backend/app/main.py, backend/{requirements.txt,alembic.ini}, backend/alembic/{env.py,script.py.mako,versions/0001_create_uploads_table.py}, backend/tests/{__init__,conftest,test_jobs}.py, backend/scripts/smoke_upload.py · 20/20 tests pass ✓, /health → {"ok":true} ✓, /api/config → 8 languages + 10 extensions ✓
- 2026-10-02 · Phase 0 complete: backend (FastAPI /health + CORS), frontend (Next.js health check page), .env.example, README.md, render.yaml, .gitignore · backend/app/{main,config,__init__}.py, backend/{requirements.txt,render.yaml}, frontend/src/app/{page,layout}.tsx, frontend/src/app/globals.css, frontend/{package.json,tsconfig.json,next.config.ts,postcss.config.mjs,eslint.config.mjs,.env.local,.gitignore}, .env.example, README.md · Backend /health → {"ok":true} ✓, CORS preflight → Access-Control-Allow-Origin: http://localhost:3000 ✓, Frontend serves at localhost:3000 ✓
- 2026-10-02 · Context.md clarifications: added /api/config endpoint, resolved open decisions, clarified attempts/files_attempts/sweeper guards, added constants.py, Python 3.13, EST_RATIO via config, 140-char snippet definition, QUEUED status documented · Context.md · no tests
- 2026-10-03 · Phase F2c: polling.ts (nextPollDelayMs backoff), usePollJob.ts (recursive setTimeout, generation counter, AbortController, reconnecting/notFound/4xx handling), jobs/[id]/page.tsx (job detail page with retry, clock tick, all component wiring). · frontend/src/lib/polling.ts, frontend/src/lib/__tests__/polling.test.ts, frontend/src/hooks/usePollJob.ts, frontend/src/app/jobs/[id]/page.tsx · lint ✔ tsc ✔ test 36/36 ✔ build ✔
**Session handoff note (≤5 lines, rewrite at the end of every session):**
  - Phase 2 complete (tag phase-2); fixtures in docs/fixtures/api_*.json.
  - F1a/F1b/F2a/F2b/F2c, F1c and F3 done.
  - F3: Created HistoryList.tsx, updated page.tsx and layout.tsx. All checks pass (lint, tsc, test, build).
  - Next: F4 (/architecture scaffold), F5 (final polish).

## 16. Human-approved addenda (override earlier text where they conflict)
1. CORS: `CORSMiddleware` with origins from `CORS_ORIGINS`, methods GET/POST/OPTIONS, `allow_headers` including `X-Session-Id` and `Content-Type` (the custom header triggers a preflight on every API call).
2. One `.env` at the REPO ROOT; `config.py` loads it by absolute path; on Render real environment variables are used (missing file must not crash). Frontend uses `frontend/.env.local` (`NEXT_PUBLIC_API_URL` only).
3. Engine: `create_async_engine(pool_size=5, max_overflow=2, pool_pre_ping=True, pool_recycle=1800)`. URL-encode the DB password.
4. Run EXACTLY ONE uvicorn process (no `--workers`): the Gnani pacing lock is per-process.
5. `render.yaml` pins `PYTHON_VERSION` (3.11.x or 3.12.x); secrets use `sync: false`.
6. Never log signed URLs, tokens or keys (including httpx debug logs). Fixtures must be scrubbed.
7. Phase 3's FIRST task: a minimal browser XHR upload to a Supabase signed URL, verified on localhost AND the Vercel URL (CORS/content-type). The Phase 1 Python smoke script does not prove this.
8. Phase 2 is split: 2a = `gnani.py` + `llm.py` + fixtures + mocked tests (no worker); 2b = worker, retry endpoints, tests, real e2e.
9. `JOBS_GLOBAL_PER_DAY` is set by the human after measuring Gnani cost per audio minute.
10. Graceful shutdown: on lifespan shutdown cancel the worker task and, best-effort, set `lease_expires_at = NULL` for the job in flight so it resumes immediately after restart (otherwise it waits out the 120 s lease).
11. `/architecture` must include a privacy note on what the LLM provider does with submitted transcripts (human checks the provider's free-tier terms).
12. A "completed step" for the attempts reset is any step that persists its outcome, including a handled 429 reschedule and a "still IN_PROGRESS" poll. Only unhandled exceptions and expired-lease reclaims leave attempts incremented.
13. Python 3.13 everywhere: render.yaml PYTHON_VERSION, README, local venv.

## 17. Agent guardrails (autonomous test loops)
Allowed files: only those named in the prompt. Everything else is READ-ONLY, including app/worker.py existing functions, app/services/, app/models.py, app/db.py, app/config.py, app/constants.py, tests/conftest.py, pyproject.toml, tests/integration/, existing test files, docs/fixtures/, alembic/. If a change to a read-only file seems necessary, STOP and ask.
Read before edit: open every file you will edit or call into in THIS thread. Never rely on memory, summaries or subagent reports (a subagent previously misdescribed config.py and conftest.py). If you use a subagent, verify each of its claims by opening the file yourself.
Test loop: run .\.venv\Scripts\python.exe -m pytest -q from backend. You may fix failures yourself, at most 3 fix iterations per failure group. Each iteration: one-line hypothesis → smallest change in allowed files → rerun the failing tests, then the full suite. After 3 failed iterations, or if the fix needs a read-only file, STOP and report: failing tests, error text, hypothesis, what you tried. No 4th try.
Never weaken tests: no deleting, skipping, xfail, loosened assertions, or expected values changed to match buggy behaviour. If a test and the spec disagree, stop and ask. Never mock the function under test.
Unit tests use no real network, DB, time.sleep over 0.05 s, or .env. Baseline: 47 unit tests pass + 18 integration deselected; any regression in existing tests → stop.
Output limits: create or edit ONE file per tool call; keep each chunk ≤ ~150 lines (split into functions or modules if larger); never print whole files in chat; final summary ≤ 10 lines.
After each checkpoint update the §15 handoff note (≤ 5 lines) so a cutoff leaves a resumable state.
No git commands, no .env, no live API calls, no integration tests, no new dependencies, no changes to pytest config.
Scheduling fields (next_run_at, lease_expires_at) are set only by the worker.py helpers (DB-side now()). processing_started_at and completed_at may use Python UTC time.

## 18. Frontend guardrails (autonomous Phase 3)
Allowed files: only those named in the prompt. Everything else is READ-ONLY: all of backend/, docs/, Context.md (except §15), package-lock.json, config files, and every file from an earlier micro-phase. If a change to a read-only file seems necessary, STOP and ask.

Contract source of truth: backend/app/schemas.py, backend/app/routes/jobs.py, backend/app/constants.py and the real captured responses docs/fixtures/api_*.json. Open them in THIS thread and copy field names, types and error codes exactly. Never invent fields. Never rely on memory, summaries or subagent reports; verify any subagent claim by opening the file yourself.

Checks (run from frontend/): npm run lint, npx tsc --noEmit, npm run build, and after F2a npm test. Fix failures yourself, at most 3 iterations per failure group (hypothesis, smallest change in allowed files, rerun). Then STOP and report: failing output, hypothesis, what you tried. Never weaken checks: no any, @ts-ignore, eslint-disable, disabled rules, deleted/skipped/loosened tests, or changed expected values.

Do not start or stop dev servers, do not call the backend with curl, no git, no .env files, no new dependencies (sole exception: devDependency vitest in F2a). No UI, markdown, state or HTTP libraries.

Contract rules: the API base URL is read ONLY in src/lib/api.ts from process.env.NEXT_PUBLIC_API_URL; every request except /health and /api/config sends header X-Session-Id (session id only via src/lib/session.ts, localStorage only there); languages, extensions, max_upload_bytes, max_duration_hint_s and est_ratio come ONLY from /api/config (no hardcoded copies, no literal language codes or extension lists outside tests); upload uses XMLHttpRequest with upload.onprogress, never fetch; the signed upload_url is never logged, printed, rendered, stored or put in error messages; no console.log; no dangerouslySetInnerHTML.

No silent failures: every failed request or upload sets visible UI state (friendly message, plus Retry only when valid). No empty catch and no catch that only logs. Unknown error codes show a generic message that includes the raw code. console.error only with redacted data.

Polling talks only to our API, uses recursive setTimeout after each completed request (never overlapping requests, never setInterval for fetching), stops on terminal states, aborts in-flight requests on unmount (AbortController), backs off on network errors, and is safe under React StrictMode double effects.

Files: one file per tool call, ≤150 lines per chunk, components ≤150 lines (split if larger), TypeScript strict, "use client" only where hooks or browser APIs are used.

After each checkpoint update the §15 handoff note (≤5 lines). Final summary ≤10 lines.

Compiling and rendering prove nothing about the real contract. The human verifies with a real browser run against the real backend; when a prompt says GATE, stop and wait.
