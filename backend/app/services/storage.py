"""
Supabase Storage REST client (httpx, async).

Four operations needed by the app:
 1. create_signed_upload_url  — for browser direct upload
 2. create_signed_download_url — for giving Gnani a public link
 3. object_exists              — verify upload completed
 4. delete_object              — clean up after transcript stored

All calls use the service-role key. Never log URLs (they contain tokens).
"""

import logging

import httpx

from app.config import SUPABASE_BUCKET, SUPABASE_SERVICE_KEY, SUPABASE_URL

logger = logging.getLogger(__name__)

# Upload URL expiry in seconds (Context.md says browser uploads right after)
_UPLOAD_EXPIRES_IN = 600  # 10 minutes

# Download URL expiry in seconds (3 h; Gnani fetches after Start, 30 min download limit)
_DOWNLOAD_EXPIRES_IN = 10800  # 3 hours


def _headers() -> dict[str, str]:
    """Common auth headers for Supabase Storage REST calls."""
    if not SUPABASE_SERVICE_KEY or not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_KEY not configured")
    return {
        "Authorization": f"Bearer {SUPABASE_SERVICE_KEY}",
        "apikey": SUPABASE_SERVICE_KEY,
    }


def _base_url() -> str:
    """Return the Supabase project URL (no trailing slash)."""
    if not SUPABASE_URL:
        raise RuntimeError("SUPABASE_URL not configured")
    return SUPABASE_URL.rstrip("/")


async def create_signed_upload_url(
    path: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> tuple[str, int]:
    """Ask Supabase for a signed URL the browser can PUT to.

    Returns (full_upload_url, expires_in_seconds).
    """
    url = f"{_base_url()}/storage/v1/object/upload/sign/{SUPABASE_BUCKET}/{path}"
    _client = client or httpx.AsyncClient()
    try:
        resp = await _client.post(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
        # Response: {"url": "/storage/v1/object/upload/sign/bucket/path?token=..."}
        relative_url = data["url"]
        full_url = f"{_base_url()}/storage/v1{relative_url}"
        return full_url, _UPLOAD_EXPIRES_IN
    finally:
        if client is None:
            await _client.aclose()


async def create_signed_download_url(
    path: str,
    *,
    expires_in: int = _DOWNLOAD_EXPIRES_IN,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Create a signed download URL for Gnani to fetch audio from.

    Returns the full public URL (Supabase returns a relative signedURL).
    """
    url = f"{_base_url()}/storage/v1/object/sign/{SUPABASE_BUCKET}/{path}"
    _client = client or httpx.AsyncClient()
    try:
        resp = await _client.post(
            url,
            headers=_headers(),
            json={"expiresIn": expires_in},
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json()
        # Response: {"signedURL": "/storage/v1/object/sign/bucket/path?token=..."}
        relative_url = data["signedURL"]
        return f"{_base_url()}/storage/v1{relative_url}"
    finally:
        if client is None:
            await _client.aclose()


async def object_exists(
    path: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Check whether an object exists in the bucket (LIST API).

    Returns True if the exact object name is in the result list.
    POST /storage/v1/object/list/{bucket}
    Body: {"prefix": "folder_path", "limit": 1, "search": "filename"}
    """
    if "/" in path:
        folder, filename = path.rsplit("/", 1)
    else:
        folder, filename = "", path

    url = f"{_base_url()}/storage/v1/object/list/{SUPABASE_BUCKET}"
    _client = client or httpx.AsyncClient()
    try:
        resp = await _client.post(
            url,
            headers={**_headers(), "Content-Type": "application/json"},
            json={"prefix": folder, "limit": 100, "search": filename},
            timeout=15,
        )
        if not (200 <= resp.status_code < 300):
            if resp.status_code in (400, 404):
                return False
            resp.raise_for_status()
        
        items = resp.json()
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict) and item.get("name") == filename:
                    return True
        return False
    finally:
        if client is None:
            await _client.aclose()


async def delete_object(
    path: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Delete an object from the bucket.

    Returns True if the object was actually removed, False if Supabase
    reported success but the object was not found (empty result list).
    Raises on HTTP errors so callers can decide how to handle failures.

    Supabase Storage REST: DELETE /storage/v1/object/{bucket}
    Body: {"prefixes": ["exact/path/to/file.ext"]}
    Response 200: JSON array of deleted object records (empty = nothing matched).
    """
    url = f"{_base_url()}/storage/v1/object/{SUPABASE_BUCKET}"
    _client = client or httpx.AsyncClient()
    try:
        resp = await _client.request(
            "DELETE",
            url,
            headers={**_headers(), "Content-Type": "application/json"},
            json={"prefixes": [path]},
            timeout=15,
        )
        resp.raise_for_status()

        # Supabase returns a JSON array of deleted objects.
        # An empty list means the path did not match any existing object
        # (Supabase still returns 200 in that case).
        deleted_items = resp.json()
        if isinstance(deleted_items, list):
            for item in deleted_items:
                if isinstance(item, dict) and item.get("name") == path:
                    logger.info("Storage object deleted (path redacted)")
                    return True

        logger.warning(
            "Storage delete returned 200 but nothing was removed (path redacted)"
        )
        return False
    finally:
        if client is None:
            await _client.aclose()
