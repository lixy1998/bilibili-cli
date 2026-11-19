"""Local "already seen" ledger — cross-run dedup for search/crawl.

Append-only JSONL under the CLI home directory, so repeated runs of the same
keyword do not keep resurfacing the same videos.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any


def cli_home() -> Path:
    """Resolve the CLI home directory (override with BILI_CLI_HOME)."""
    override = os.getenv("BILI_CLI_HOME")
    if override:
        return Path(override).expanduser()
    return Path.home() / ".bilibili-cli"


def ledger_path() -> Path:
    return cli_home() / "seen.jsonl"


def load() -> dict[str, dict[str, Any]]:
    """Load the ledger as {bvid: record}; later records win."""
    path = ledger_path()
    if not path.exists():
        return {}

    records: dict[str, dict[str, Any]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        bvid = record.get("bvid")
        if bvid:
            records[bvid] = record
    return records


def mark(entries: list[dict[str, Any]]) -> int:
    """Append unseen videos to the ledger; returns how many were added."""
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = load()
    added = 0
    with path.open("a", encoding="utf-8") as handle:
        for entry in entries:
            bvid = entry.get("bvid")
            if not bvid or bvid in existing:
                continue
            record = {
                "bvid": bvid,
                "title": entry.get("title", ""),
                "up": entry.get("author", ""),
                "note": entry.get("note", ""),
                "at": datetime.now().isoformat(timespec="seconds"),
            }
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            existing[bvid] = record
            added += 1
    return added


def clear() -> int:
    """Delete the ledger; returns the number of previously stored records."""
    path = ledger_path()
    if not path.exists():
        return 0
    count = len(load())
    path.unlink()
    return count
