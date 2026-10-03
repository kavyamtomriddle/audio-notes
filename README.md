# Audio Notes

> Upload any audio file. Get a transcript and an AI summary. Past uploads are saved and reopenable.

**Live app → [audio-notes.vercel.app](https://audio-notes-red.vercel.app)**  
**Architecture → [/architecture](https://audio-notes-red.vercel.app/architecture)**  
**Repo → [github.com/kavyamtomriddle/audio-notes](https://github.com/kavyamtomriddle/audio-notes)**

---

## What it does

| Step | What happens |
|---|---|
| 1 | You pick an audio file (mp3, wav, m4a, up to 50 MB) |
| 2 | The browser uploads it directly to Supabase Storage via a signed URL |
| 3 | A background worker sends it to the **Gnani Batch STT API** for transcription |
| 4 | The raw transcript is stored, then passed to **Gemini** for a structured summary |
| 5 | The job page polls every 3 s and shows live stage text + a progress bar |
| 6 | Transcript and summary appear when done. Past uploads are listed by session |

The audio file is deleted from storage after the transcript is saved.

---

## Tech stack

| Layer | Choice |
|---|---|
| Frontend | Next.js 15 (App Router) · TypeScript · Tailwind CSS · Vercel |
| Backend | FastAPI · Python 3.13 · SQLAlchemy 2 async · Render |
| Database | Postgres on Supabase (session pooler) |
| Storage | Supabase Storage (private bucket) |
| ASR | Gnani Batch STT API (`gnani-prisma-v2.5`, `en-IN` default) |
| LLM | Google Gemini (map-reduce for long transcripts) |
| Queue | Postgres `FOR UPDATE SKIP LOCKED` + 120 s lease (no Redis/Celery) |

---

## Project layout

```
/
├── backend/
│   ├── app/
│   │   ├── main.py          # FastAPI app + worker lifespan
│   │   ├── config.py        # Env loading (one .env at repo root)
│   │   ├── worker.py        # Claim / finish / fail / release helpers
│   │   ├── loop.py          # Worker loop + sweeper
│   │   ├── routes/jobs.py   # All API routes
│   │   ├── services/        # gnani.py, llm.py, storage.py
│   │   └── steps/           # queued, transcribing, completed, summarizing
│   ├── alembic/             # DB migrations
│   ├── scripts/             # smoke_upload.py, e2e_worker.py, gnani_live_check.py
│   ├── tests/               # ~47 unit tests + 18 integration tests
│   ├── requirements.txt
│   └── render.yaml
├── frontend/
│   ├── src/
│   │   ├── app/             # page.tsx, jobs/[id]/page.tsx, architecture/page.tsx
│   │   ├── components/      # UploadForm, HistoryList, JobStatus, TranscriptPanel, SummaryPanel, ErrorPanel
│   │   ├── hooks/           # useConfig, useHealth, usePollJob
│   │   └── lib/             # api.ts, upload.ts, stage.ts, session.ts, errors.ts
│   └── package.json
├── docs/
│   ├── architecture.md      # Full architecture prose (source for /architecture page)
│   └── fixtures/            # Scrubbed real API responses used in tests
├── .env.example             # All env vars with empty values
└── Context.md               # Agent context and progress log
```

---

## Run locally

### 1. Clone and set up environment

```bash
git clone https://github.com/kavyamtomriddle/audio-notes.git
cd audio-notes
cp .env.example .env
# Fill in DATABASE_URL, SUPABASE_URL, SUPABASE_SERVICE_KEY,
# GNANI_API_KEY, LLM_API_KEY, LLM_MODEL in .env
```

### 2. Backend

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS/Linux

pip install -r requirements.txt
alembic upgrade head             # run DB migrations once
uvicorn app.main:app --port 8000
```

Health check: `curl http://localhost:8000/health` → `{"ok":true}`

Set `ENABLE_WORKER=true` in `.env` to also run the background job processor.  
If you want backend logs without the worker (e.g. while testing the frontend against Render), set `ENABLE_WORKER=false`.

### 3. Frontend

```bash
cd frontend
npm install
# create frontend/.env.local:
echo "NEXT_PUBLIC_API_URL=http://localhost:8000" > .env.local
npm run dev
```

Opens at `http://localhost:3000`.  
To test against the production backend instead, set `NEXT_PUBLIC_API_URL=https://audio-notes-n5b2.onrender.com`.

---

## Tests

```bash
# Unit tests (no network, no DB)
cd backend
pytest -q

# Integration tests (real Postgres, throwaway schema — requires DATABASE_URL)
pytest -m integration tests/integration/test_claim.py -v

# Frontend
cd frontend
npm test
```

The integration tests create a temporary `itest_*` schema in your Postgres database and drop it on teardown. They never touch `public.uploads`.

### Live scripts (spend Gnani credits)

```bash
# Smoke test: upload a file, verify it reaches storage, clean up
python scripts/smoke_upload.py --api-url https://audio-notes-n5b2.onrender.com

# End-to-end: upload + wait for completed status
python scripts/e2e_worker.py --api-url https://audio-notes-n5b2.onrender.com \
  --file path/to/audio.mp3 --expect completed

# Check Gnani API key is valid and credits remain
python scripts/gnani_live_check.py
```

---

## Deployment

### Backend (Render)

- Service type: **Web Service**, runtime: **Python 3.13**
- Build command: `pip install -r requirements.txt`
- Start command: `uvicorn app.main:app --host 0.0.0.0 --port $PORT`
- Set all env vars from `.env.example` in the Render dashboard (never commit `.env`)
- Set `ENABLE_WORKER=true` on Render so the background processor runs

### Frontend (Vercel)

- Root directory: `frontend`
- Set `NEXT_PUBLIC_API_URL` to your Render backend URL in the Vercel dashboard
- No other env vars needed on the frontend

---

## Key design decisions

**Why Gnani Batch STT, not REST?**  
The REST endpoint rejects audio longer than ~30 seconds. Batch accepts a signed cloud storage URL, so Gnani fetches the file directly — our backend never buffers audio in memory.

**Why Postgres as the job queue?**  
`FOR UPDATE SKIP LOCKED` gives safe concurrent claiming without Redis or Celery. A 120-second lease means a crashed worker's job is automatically reclaimable.

**Why direct browser → Supabase upload?**  
Vercel and Render both enforce request body size limits. Bypassing them with a signed URL means 50 MB files work without any proxy infrastructure.

**Why polling, not webhooks?**  
The Render free tier sleeps when idle. A sleeping instance misses webhook callbacks. Polling is idempotent and resumable regardless of server state.

See [/architecture](https://audio-notes-red.vercel.app/architecture) for the full write-up.

---

## Known limitations

- Render free tier sleeps after inactivity (~1 min cold start). The worker only runs while the instance is awake.
- 50 MB per-file ceiling.
- No authentication — jobs are scoped to an anonymous browser session ID.
- Transcripts have no punctuation (raw ASR output).
- No language auto-detection; the user selects the language.
- Audio deletion after transcription is best-effort; a failed delete leaves the file in the bucket.

---

## License

MIT
