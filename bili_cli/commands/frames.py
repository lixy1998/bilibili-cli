"""Frame extraction command (storyboard based, no full video download)."""

from __future__ import annotations

from pathlib import Path

import click

from .. import ledger, storyboard
from . import common


@click.command()
@click.argument("bv_or_url")
@click.option(
    "--max",
    "-n",
    "count",
    default=3,
    type=click.IntRange(1, storyboard.MAX_FRAME_COUNT),
    help="抽取帧数 (默认 3，最多 12)。",
)
@click.option("--at", type=float, default=None, help="指定秒数，取最接近的一帧（与 --max 二选一优先）。")
@click.option("--page", "page_index", default=0, type=click.IntRange(0), help="分P序号 (从 0 开始)。")
@click.option("-o", "--output-dir", "out_dir", default=None, help="保存目录（默认 ~/.bilibili-cli/frames/<BV号>）。")
@common.structured_output_options
def frames(
    bv_or_url: str,
    count: int,
    at: float | None,
    page_index: int,
    out_dir: str | None,
    as_json: bool,
    as_yaml: bool,
):
    """抽取视频画面帧（走故事板，不下整片）。

    适合判断「没有字幕的演示类视频到底演了什么」。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)
    bvid = common.extract_bvid_or_exit(bv_or_url)

    meta = common.run_or_exit(
        client.get_video_storyboard(bvid, page_index=page_index), "获取故事板失败"
    )

    target_dir = (
        Path(out_dir).expanduser()
        if out_dir
        else ledger.cli_home() / "frames" / bvid
    )

    results = common.run_or_exit(
        storyboard.extract_frames(meta, bvid, target_dir, count=count, at=at),
        "抽帧失败",
    )

    payload = {
        "bvid": bvid,
        "requested": 1 if at is not None else count,
        "count": len(results),
        "frames": results,
        "storyboard": {
            "sheets": len(meta.get("image") or []),
            "grid": f"{meta.get('img_x_len')}x{meta.get('img_y_len')}",
            "frame_size": f"{meta.get('img_x_size')}x{meta.get('img_y_size')}",
            "total_frames": len(meta.get("index") or []),
        },
    }
    if common.emit_structured(payload, output_format):
        return

    if not results:
        common.console.print("[yellow]未取到任何画面帧[/yellow]")
        return

    common.console.print(f"[bold]🖼  {bvid} 画面帧[/bold]  →  {target_dir}")
    for item in results:
        common.console.print(f"  [cyan]{item['at_label']}[/cyan]  {item['path']}")
