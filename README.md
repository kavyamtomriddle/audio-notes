# Audio Notes Platform

Web platform: upload audio → Gnani ASR transcription → LLM summary → past uploads listed and reopenable.

## Prerequisites

- **Python 3.13** (backend)
- **Node.js 18+** and npm (frontend)

## Setup

### 1. Environment variables

Copy `.env.example` to `.env` at the repo root and fill in the secret values:

```bash
cp .env.example .env
# Edit .env with your real credentials
```

For the frontend, create `frontend/.env.local`:

```
NEXT_PUBLIC_API_URL=http://localhost:8000
```

### 2. Backend

```bash
cd backend
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

Health check: `GET http://localhost:8000/health` → `{"ok": true}`

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Opens at `http://localhost:3000`. The home page calls the backend `/health` endpoint and shows "Backend: ok" or "waking up…".

## Deployment

- **Backend** → Render (native Python, see `backend/render.yaml`). Set env vars in the Render dashboard.
- **Frontend** → Vercel (root directory: `frontend`). Set `NEXT_PUBLIC_API_URL` in the Vercel dashboard.

## Project structure

```
/Context.md         Master context & state (source of truth)
/backend/           FastAPI app (Python 3.13)
  app/main.py       Application entry point
  app/config.py     Environment loading
  requirements.txt  Python dependencies
  render.yaml       Render deployment blueprint
/frontend/          Next.js App Router (TypeScript + Tailwind)
/.env.example       All env vars with empty secret values
```
