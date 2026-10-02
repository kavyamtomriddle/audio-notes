"""
Smoke-test script: end-to-end upload via real Supabase.

Usage:
    python scripts/smoke_upload.py                 # run and clean up
    python scripts/smoke_upload.py --keep           # run and leave the row + object

What it does:
  1. POST /api/jobs/initiate  with a tiny real audio file
  2. PUT the file to the returned Supabase signed upload URL
  3. POST /api/jobs/{id}/complete
  4. GET /api/jobs/{id}  and print the job row
  5. Unless --keep: DELETE the row from DB and the object from storage

Run this against a real backend (local or deployed) with real Supabase credentials.
Set BACKEND_URL env var (default http://localhost:8000).
"""

import argparse
import asyncio
import os
import struct
import sys
import uuid

# Add project root to path so we can import app modules for cleanup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx


def _make_tiny_wav() -> bytes:
    """Create a minimal valid WAV file (44-byte header + 100 bytes of silence).

    This is a real WAV that any audio parser will accept.
    """
    num_channels = 1
    sample_rate = 8000
    bits_per_sample = 8
    data_size = 100  # 100 bytes of silence

    byte_rate = sample_rate * num_channels * bits_per_sample // 8
    block_align = num_channels * bits_per_sample // 8
    chunk_size = 36 + data_size

    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF", chunk_size, b"WAVE",
        b"fmt ", 16, 1,  # PCM format
        num_channels, sample_rate, byte_rate, block_align, bits_per_sample,
        b"data", data_size,
    )
    return header + b"\x80" * data_size  # 0x80 = silence in unsigned 8-bit PCM


async def main():
    parser = argparse.ArgumentParser(description="Smoke-test upload flow")
    parser.add_argument("--keep", action="store_true", help="Don't clean up after")
    args = parser.parse_args()

    backend_url = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
    session_id = f"smoke-{uuid.uuid4().hex[:8]}"

    print(f"Backend: {backend_url}")
    print(f"Session: {session_id}")

    wav_data = _make_tiny_wav()
    print(f"WAV file: {len(wav_data)} bytes")

    async with httpx.AsyncClient(timeout=30) as client:
        # 1. Initiate
        print("\n--- 1. POST /api/jobs/initiate ---")
        resp = await client.post(
            f"{backend_url}/api/jobs/initiate",
            headers={"X-Session-Id": session_id},
            json={
                "filename": "smoke_test.wav",
                "size_bytes": len(wav_data),
                "content_type": "audio/wav",
                "language_code": "en-IN",
                "duration_hint_s": 0.0125,  # 100 bytes / 8000 Hz
            },
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code != 201:
            print(f"Error: {resp.text}")
            sys.exit(1)
        initiate_data = resp.json()
        job_id = initiate_data["id"]
        upload_url = initiate_data["upload_url"]
        print(f"Job ID: {job_id}")
        print(f"Upload URL length: {len(upload_url)} chars (not printing — contains token)")

        # 2. PUT to Supabase signed upload URL
        print("\n--- 2. PUT file to signed upload URL ---")
        resp = await client.put(
            upload_url,
            content=wav_data,
            headers={"Content-Type": "audio/wav"},
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code not in (200, 201):
            print(f"Upload failed: {resp.text}")
            print("NOTE: If this is multipart/form-data required, update Context.md §4/§11!")
            sys.exit(1)
        print("Upload successful!")

        # 3. Complete
        print("\n--- 3. POST /api/jobs/{id}/complete ---")
        resp = await client.post(
            f"{backend_url}/api/jobs/{job_id}/complete",
            headers={"X-Session-Id": session_id},
        )
        print(f"Status: {resp.status_code}")
        if resp.status_code != 200:
            print(f"Error: {resp.text}")
            sys.exit(1)
        print(f"Result: {resp.json()}")

        # 4. Get job
        print("\n--- 4. GET /api/jobs/{id} ---")
        resp = await client.get(
            f"{backend_url}/api/jobs/{job_id}",
            headers={"X-Session-Id": session_id},
        )
        print(f"Status: {resp.status_code}")
        job_data = resp.json()
        for key in ("id", "filename", "size_bytes", "status", "storage_path", "created_at"):
            if key in job_data:
                # Redact storage_path to avoid printing signed URLs
                if key == "storage_path":
                    print(f"  {key}: (set, not printed)")
                else:
                    print(f"  {key}: {job_data[key]}")

        # 5. Clean up (unless --keep)
        if not args.keep:
            print("\n--- 5. Cleanup ---")
            # Delete via the storage service directly (we need app config for this)
            try:
                from app.services.storage import delete_object
                storage_path = job_data.get("storage_path", "")
                if storage_path:
                    deleted = await delete_object(storage_path)
                    print(f"Storage object deleted: {deleted}")
            except Exception as e:
                print(f"Storage cleanup failed: {e}")

            # Delete the DB row via SQL (we need the DB)
            try:
                from app.db import get_engine
                from sqlalchemy import text as sql_text
                engine = get_engine()
                async with engine.begin() as conn:
                    await conn.execute(
                        sql_text("DELETE FROM uploads WHERE id = :id"),
                        {"id": job_id},
                    )
                print(f"DB row deleted: {job_id}")
            except Exception as e:
                print(f"DB cleanup failed (may need manual cleanup): {e}")
        else:
            print("\n--keep specified, skipping cleanup")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
