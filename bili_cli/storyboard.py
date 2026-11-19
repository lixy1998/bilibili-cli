"""Storyboard frame extraction — grab a few real frames without downloading video."""

from __future__ import annotations

import asyncio
from io import BytesIO
from pathlib import Path
from typing import Any

import aiohttp

DEFAULT_FRAME_COUNT = 3
MAX_FRAME_COUNT = 12


def _pick_frames(index: list[int], count: int, at: float | None) -> list[int]:
    """Choose frame numbers to extract from the storyboard timeline."""
    total = len(index)
    if total == 0:
        return []

    if at is not None:
        target = max(0.0, float(at))
        best = min(range(total), key=lambda i: abs(index[i] - target))
        return [best]

    count = max(1, min(count, MAX_FRAME_COUNT, total))
    if count == 1:
        return [total // 2]

    picked: list[int] = []
    for k in range(count):
        position = round(k * (total - 1) / (count - 1))
        if position not in picked:
            picked.append(position)
    return picked


async def extract_frames(
    storyboard: dict[str, Any],
    bvid: str,
    out_dir: Path,
    count: int = DEFAULT_FRAME_COUNT,
    at: float | None = None,
) -> list[dict[str, Any]]:
    """Crop selected frames out of the storyboard sheets and save them as PNG."""
    try:
        from PIL import Image
    except ImportError as e:  # pragma: no cover - Pillow is optional at runtime
        raise RuntimeError(
            "抽帧需要 Pillow：pip install pillow（bilibili-cli[frames]）"
        ) from e

    images: list[str] = list(storyboard.get("image") or [])
    index: list[int] = [int(x) for x in (storyboard.get("index") or [])]
    x_len = int(storyboard.get("img_x_len") or 10)
    y_len = int(storyboard.get("img_y_len") or 10)
    frame_w = int(storyboard.get("img_x_size") or 480)
    frame_h = int(storyboard.get("img_y_size") or 270)

    per_sheet = max(x_len * y_len, 1)
    frame_numbers = _pick_frames(index, count, at)
    if not frame_numbers:
        return []

    out_dir.mkdir(parents=True, exist_ok=True)

    needed_sheets = sorted({fn // per_sheet for fn in frame_numbers})
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://www.bilibili.com"}
    sheet_bytes: dict[int, bytes] = {}

    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        for sheet_no in needed_sheets:
            if sheet_no >= len(images):
                continue
            url = images[sheet_no]
            if url.startswith("//"):
                url = "https:" + url
            try:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    sheet_bytes[sheet_no] = await resp.read()
            except (aiohttp.ClientError, asyncio.TimeoutError):
                continue

    results: list[dict[str, Any]] = []
    for frame_no in frame_numbers:
        sheet_no = frame_no // per_sheet
        raw = sheet_bytes.get(sheet_no)
        if raw is None:
            continue

        within = frame_no % per_sheet
        row, col = divmod(within, x_len)

        try:
            with Image.open(BytesIO(raw)) as sheet:
                left, top = col * frame_w, row * frame_h
                frame = sheet.crop((left, top, left + frame_w, top + frame_h))
                seconds = index[frame_no] if frame_no < len(index) else 0
                minutes, secs = divmod(int(seconds), 60)
                name = f"{bvid}_f{frame_no:04d}_{minutes:02d}m{secs:02d}s.png"
                path = out_dir / name
                frame.save(path, format="PNG")
        except (OSError, ValueError):
            continue

        results.append(
            {
                "frame": frame_no,
                "at": seconds,
                "at_label": f"{minutes:02d}:{secs:02d}",
                "path": str(path),
            }
        )

    return results
