"""
E2E worker script for Audio Notes Platform.

Usage:
    python scripts/e2e_worker.py --file path/to/audio.mp3
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from urllib.parse import urlparse

# Add project root to path so we can import app modules for cleanup
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx


async def _cleanup(job_id: str | None, storage_path: str) -> None:
    """Delete the storage object and DB row."""
    # Delete storage object if present
    if storage_path:
        try:
            from app.services.storage import delete_object, object_exists
            still_exists = await object_exists(storage_path)
            print(f"Storage object exists before cleanup: {still_exists}")
            if still_exists:
                deleted = await delete_object(storage_path)
                print(f"Storage delete returned: {deleted}")
        except Exception as e:
            print(f"Storage cleanup failed with exception: {e}")

    # Delete DB row
    if job_id:
        try:
            from app.db import get_engine
            from sqlalchemy import text as sql_text
            engine = get_engine()
            async with engine.begin() as conn:
                res = await conn.execute(
                    sql_text("DELETE FROM uploads WHERE id = :id"),
                    {"id": job_id},
                )
                print(f"DB row delete returned: {res.rowcount} rows affected")
        except Exception as e:
            print(f"DB cleanup failed: {e}")


async def main():
    parser = argparse.ArgumentParser(description="End-to-end worker test")
    parser.add_argument("--file", required=True, help="Path to audio file")
    parser.add_argument("--api-url", default="http://localhost:8000", help="Backend API URL")
    parser.add_argument("--language", default="en-IN", help="Language code")
    parser.add_argument("--expect", default="completed", help="Expected outcome (completed, completed:summary_failed, failed:<ERROR_CODE>, rejected:<ERROR_CODE>)")
    parser.add_argument("--keep", action="store_true", help="Don't clean up after")
    parser.add_argument("--timeout-s", type=int, default=1800, help="Timeout in seconds")
    args = parser.parse_args()

    api_url = args.api_url.rstrip("/")
    session_id = f"e2e-{uuid.uuid4().hex[:8]}"

    print(f"Backend: {api_url}")
    print(f"Session: {session_id}")
    print(f"File: {args.file}")
    
    if not os.path.isfile(args.file):
        print(f"File not found: {args.file}")
        sys.exit(1)

    file_size = os.path.getsize(args.file)
    filename = os.path.basename(args.file)
    
    # Guess content type
    ext = filename.split(".")[-1].lower() if "." in filename else ""
    content_type = f"audio/{ext}" if ext else "application/octet-stream"

    job_id: str | None = None
    storage_path: str = ""

    cleanup_allowed = False

    start_time = time.time()
    
    try:
        with open(args.file, "rb") as f:
            file_data = f.read()

        async with httpx.AsyncClient(timeout=30) as client:
            # 1. Initiate
            print("\n--- 1. POST /api/jobs/initiate ---")
            resp = await client.post(
                f"{api_url}/api/jobs/initiate",
                headers={"X-Session-Id": session_id},
                json={
                    "filename": filename,
                    "size_bytes": file_size,
                    "content_type": content_type,
                    "language_code": args.language,
                },
            )
            
            if resp.status_code not in (200, 201):
                print(f"Status: {resp.status_code}")
                print(f"Error body: {resp.text}")
                
                try:
                    err_code = resp.json().get("error_code")
                except:
                    err_code = ""
                    
                if args.expect.startswith("rejected:") and args.expect.split(":", 1)[1] == err_code:
                    print("Matched expected rejection.")
                    cleanup_allowed = True
                    sys.exit(0)
                else:
                    print("Unexpected rejection.")
                    sys.exit(1)

            initiate_data = resp.json()
            job_id = initiate_data["id"]
            upload_url = initiate_data["upload_url"]
            storage_path = "/".join(urlparse(upload_url).path.split("/")[-3:])
            
            print(f"Job ID: {job_id}")
            print(f"Upload URL length: {len(upload_url)} chars (not printing — contains token)")

            # 2. Upload
            print("\n--- 2. PUT file to signed upload URL ---")
            resp = await client.put(
                upload_url,
                content=file_data,
                headers={"Content-Type": content_type},
            )
            print(f"Status: {resp.status_code}")
            if resp.status_code not in (200, 201):
                print(f"Upload failed: {resp.text}")
                sys.exit(1)
            print("Upload successful!")

            # 3. Complete
            print("\n--- 3. POST /api/jobs/{id}/complete ---")
            resp = await client.post(
                f"{api_url}/api/jobs/{job_id}/complete",
                headers={"X-Session-Id": session_id},
            )
            print(f"Status: {resp.status_code}")
            if resp.status_code != 200:
                print(f"Error: {resp.text}")
                sys.exit(1)

            # 4. Poll
            print("\n--- 4. Polling GET /api/jobs/{id} ---")
            
            terminal = False
            job_data = {}
            
            while not terminal:
                elapsed = int(time.time() - start_time)
                if elapsed > args.timeout_s:
                    print(f"Timeout reached ({args.timeout_s}s)")
                    sys.exit(2)
                    
                resp = await client.get(
                    f"{api_url}/api/jobs/{job_id}",
                    headers={"X-Session-Id": session_id},
                )
                
                if resp.status_code != 200:
                    print(f"{elapsed}s\tHTTP {resp.status_code}")
                else:
                    job_data = resp.json()
                    status = job_data.get("status")
                    gnani_status = job_data.get("gnani_status")
                    summary_status = job_data.get("summary_status")
                    
                    print(f"{elapsed}s\t{status}\t{gnani_status}\t{summary_status}")
                    
                    if status == "failed" or (status == "completed" and summary_status in ("done", "failed")):
                        terminal = True
                        break
                        
                await asyncio.sleep(3)

            print("\n--- Final Status ---")
            status = job_data.get("status")
            error_code = job_data.get("error_code")
            summary_status = job_data.get("summary_status")
            transcript = job_data.get("transcript") or ""
            summary = job_data.get("summary") or ""
            
            cleanup_allowed = True
            
            print(f"Job ID: {job_id}")
            print(f"Session ID: {session_id}")
            print(f"Status: {status}")
            print(f"Error Code: {error_code}")
            print(f"Summary Status: {summary_status}")
            print(f"Transcript (first 120 chars): {transcript[:120]}")
            print(f"Summary (first 120 chars): {summary[:120]}")
            
            # Check expectation
            matched = False
            if args.expect == "completed" and status == "completed" and summary_status == "done":
                matched = True
            elif args.expect == "completed:summary_failed" and status == "completed" and summary_status == "failed":
                matched = True
            elif args.expect.startswith("failed:") and status == "failed" and args.expect.split(":", 1)[1] == error_code:
                matched = True
                
            if matched:
                print("Matched expected outcome.")
            else:
                print(f"Outcome did not match expectation: {args.expect}")
                sys.exit(1)

    except (asyncio.CancelledError, KeyboardInterrupt):
        print(f"\nInterrupted: NOT cleaning up (job may still be in flight). Job id: {job_id}, storage path: {storage_path}")
        sys.exit(130)
    finally:
        if cleanup_allowed and not args.keep:
            print("\n--- Cleanup ---")
            await _cleanup(job_id, storage_path)
            
if __name__ == "__main__":
    asyncio.run(main())
