from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import CORS_ORIGINS

app = FastAPI(title="Audio Notes API")

# Browser calls from the Vercel site / localhost. X-Session-Id is a custom header, so it
# triggers a preflight on every API call and MUST be listed here (Context.md 16.1).
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["X-Session-Id", "Content-Type"],
)


@app.get("/health")
async def health():
    return {"ok": True}
