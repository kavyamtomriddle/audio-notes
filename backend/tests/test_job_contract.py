"""
test_job_contract.py — unit test for the job-dict / Upload-column contract.

Fix A, test (1): regex-extract every key read via job["x"] or job.get("x")
across all app/steps/*.py files and assert each is a column of the Upload
model (Context.md §5 / §17).

No DB, no network, no .env, no mocking of the function under test.
This test will catch future step code that reads a column that does not exist.
"""

from __future__ import annotations

import re
from pathlib import Path


# ---------------------------------------------------------------------------
# Collect all steps/*.py files
# ---------------------------------------------------------------------------

_STEPS_DIR = Path(__file__).parent.parent / "app" / "steps"

# Regex: matches job["column_name"] and job.get("column_name")
# Captures the column name (group 1).
_KEY_RE = re.compile(
    r'\bjob\s*(?:\[\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\'\s*\]]|\.get\s*\(\s*["\']([A-Za-z_][A-Za-z0-9_]*)["\'])'
)


def _extract_keys_from_file(path: Path) -> set[str]:
    """Return all job[...] / job.get(...) column names found in a source file."""
    text = path.read_text(encoding="utf-8")
    keys: set[str] = set()
    for m in _KEY_RE.finditer(text):
        # group(1) matches job["key"], group(2) matches job.get("key")
        key = m.group(1) or m.group(2)
        if key:
            keys.add(key)
    return keys


def _collect_all_step_keys() -> dict[str, set[str]]:
    """Map filename → set of column keys read via job[...] or job.get(...)."""
    result: dict[str, set[str]] = {}
    for py_file in sorted(_STEPS_DIR.glob("*.py")):
        if py_file.name.startswith("_") and py_file.name != "__init__.py":
            continue
        keys = _extract_keys_from_file(py_file)
        if keys:
            result[py_file.name] = keys
    return result


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_steps_only_read_real_columns():
    """
    Every key accessed via job["x"] or job.get("x") in app/steps/*.py
    must be a real column of the Upload ORM model.

    This is a static-analysis test: it reads source files and the ORM
    definition, does no I/O against a live DB.
    """
    from app.models import Upload  # noqa: PLC0415 (import inside test intentional)

    # Collect the real column names from the ORM model.
    real_columns: set[str] = {col.key for col in Upload.__table__.columns}

    file_keys = _collect_all_step_keys()

    # Ensure we found at least one file (guards against directory mis-config).
    assert file_keys, (
        f"No job-key accesses found in {_STEPS_DIR!s}; "
        "check that the steps directory is correct."
    )

    violations: list[str] = []
    for filename, keys in sorted(file_keys.items()):
        bad_keys = keys - real_columns
        for key in sorted(bad_keys):
            violations.append(f"  {filename}: job[{key!r}] — not a column of Upload")

    assert not violations, (
        "The following step files access job keys that are NOT columns of "
        f"the Upload model:\n" + "\n".join(violations)
    )
