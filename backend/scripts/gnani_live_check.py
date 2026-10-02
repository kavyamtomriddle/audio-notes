"""
Live Gnani integration check script.

Usage:
    python scripts/gnani_live_check.py --file path/to/audio.mp3

What it does (spends Gnani credits):
  1. Upload the file through storage.py to a `live-check-{uuid}` path.
  2. Create a signed download URL.
  3. Run gnani.py: create → start → poll (every 10 s) → files → transcript.
  4. Print only statuses and the first 80 characters of the transcript.
  5. Delete the storage object and CONFIRM it's gone via object_exists.

Does NOT touch the uploads table — this is a pure storage + Gnani + transcript test.
"""

import argparse
import asyncio
import os
import sys
import uuid

# Add project root to path so we can import app modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx

from app.services import gnani, storage


async def main():
    parser = argparse.ArgumentParser(description="Live Gnani integration check")
    parser.add_argument("--file", required=True, help="Path to audio file")
    args = parser.parse_args()

    filepath = args.file
    if not os.path.isfile(filepath):
        print(f"File not found: {filepath}")
        sys.exit(1)

    filename = os.path.basename(filepath)
    file_size = os.path.getsize(filepath)
    print(f"File: {filename} ({file_size} bytes)")

    # Unique storage path that won't collide with real uploads
    object_path = f"live-check-{uuid.uuid4().hex[:8]}/{filename}"

    job_id = "UNKNOWN"
    terminal_reached = False

    async with httpx.AsyncClient(timeout=60) as client:
        try:
            # --- Step 1: Upload to Supabase storage ---
            print("\n--- 1. Upload to storage ---")
            upload_url, _ = await storage.create_signed_upload_url(
                object_path, client=client,
            )
            print(f"Signed upload URL obtained (length: {len(upload_url)})")

            with open(filepath, "rb") as f:
                file_data = f.read()

            resp = await client.put(
                upload_url,
                content=file_data,
                headers={"Content-Type": "application/octet-stream"},
            )
            if resp.status_code not in (200, 201):
                print(f"Upload failed: HTTP {resp.status_code}")
                sys.exit(1)
            print("Upload OK")

            # Verify exists
            exists = await storage.object_exists(object_path, client=client)
            print(f"Object exists: {exists}")

            # --- Step 2: Signed download URL ---
            print("\n--- 2. Signed download URL ---")
            download_url = await storage.create_signed_download_url(
                object_path, client=client,
            )
            print(f"Download URL length: {len(download_url)} (not printing — contains token)")

            # --- Step 3: Gnani create job ---
            print("\n--- 3. Gnani create job ---")
            create_resp = await gnani.create_job(
                download_url, "en-IN", client=client,
            )
            job_id = create_resp["job_id"]
            print(f"Job ID: {job_id}")
            print(f"Status: {create_resp.get('status')}")

            # --- Step 4: Gnani start job ---
            print("\n--- 4. Gnani start job ---")
            start_resp = await gnani.start_job(job_id, client=client)
            print(f"Status: {start_resp.get('status')}")

            # --- Step 5: Poll until terminal ---
            print("\n--- 5. Polling (every 10 s) ---")
            terminal_statuses = {
                "COMPLETED", "PARTIAL_FAILURE", "FAILED",
                "START_FAILED", "CANCELLED",
            }
            max_polls = 60  # 10 minutes max
            for poll_num in range(1, max_polls + 1):
                await asyncio.sleep(10)
                job_status = await gnani.get_job(job_id, client=client)
                status = job_status.get("status", "UNKNOWN")
                progress = job_status.get("progress", {})
                pct = progress.get("percent", "?")
                print(f"  Poll {poll_num}: status={status}, percent={pct}")
                if status in terminal_statuses:
                    break
            else:
                print("Max polls exceeded — giving up")
                return

            if status in terminal_statuses:
                terminal_reached = True

            if status != "COMPLETED":
                print(f"Job ended with status: {status}")
                # Try to get error details
                try:
                    files_resp = await gnani.get_files(job_id, client=client)
                    data = files_resp.get("data", [])
                    if data:
                        print(f"File error: {data[0].get('error_message')}")
                except Exception as e:
                    print(f"Could not get files: {e}")
                return

            # --- Step 6: Get files ---
            print("\n--- 6. Get files ---")
            files_resp = await gnani.get_files(job_id, client=client)
            data = files_resp.get("data", [])
            if not data:
                print("No file data returned")
                return

            file_info = data[0]
            duration = gnani.parse_duration_seconds(
                file_info.get("duration_seconds"),
            )
            print(f"File status: {file_info.get('status')}")
            print(f"Duration: {duration}s")

            transcript_url = file_info.get("transcript_url")
            if not transcript_url:
                print(f"No transcript URL. Error: {file_info.get('error_message')}")
                return

            # --- Step 7: Fetch transcript ---
            print("\n--- 7. Fetch transcript ---")
            try:
                transcript = await gnani.fetch_transcript(
                    transcript_url, client=client,
                )
                print(f"Transcript (first 80 chars): {transcript[:80]}")
                print(f"Total length: {len(transcript)} chars")
            except gnani.EmptyTranscriptError:
                print("Empty transcript — NO_SPEECH_DETECTED")
            
            terminal_reached = True

        except Exception as e:
            print(f"\nUnexpected error: {e}")
            raise
        finally:
            # --- Cleanup: delete object and confirm ---
            print("\n--- Cleanup ---")
            if not terminal_reached:
                print(f"Job ID: {job_id}")
                print(f"Object Path: {object_path}")
                print("Not deleting audio object as Gnani job may still be fetching it.")
            else:
                try:
                    deleted = await storage.delete_object(
                        object_path, client=client,
                    )
                    print(f"Delete returned: {deleted}")
    
                    # DIAGNOSTIC: Make the same GET request the old object_exists made
                    # to see why it returned True (false positive).
                    diag_url = f"{storage._base_url()}/storage/v1/object/authenticated/{storage.SUPABASE_BUCKET}/{object_path}"
                    diag_resp = await client.get(
                        diag_url,
                        headers=storage._headers(),
                        follow_redirects=True,
                    )
                    print(f"Diagnostic exists status: {diag_resp.status_code}")
                    print(f"Diagnostic exists body: {diag_resp.text[:200]}")
    
                    still_exists = await storage.object_exists(
                        object_path, client=client,
                    )
                    if still_exists:
                        print("WARNING: Object still exists after delete!")
                    else:
                        print("Confirmed: object is gone")
                except Exception as e:
                    print(f"Cleanup error: {e}")

    print("\nDone!")


if __name__ == "__main__":
    asyncio.run(main())
