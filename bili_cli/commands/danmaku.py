"""Danmaku (bullet comment) command."""

from __future__ import annotations

import click

from . import common


@click.command()
@click.argument("bv_or_url")
@click.option("--max", "-n", "count", default=100, type=click.IntRange(1), help="显示数量 (默认 100)。")
@click.option("--page", "page_index", default=0, type=click.IntRange(0), help="分P序号 (从 0 开始，默认 0)。")
@common.structured_output_options
def danmaku(
    bv_or_url: str,
    count: int,
    page_index: int,
    as_json: bool,
    as_yaml: bool,
):
    """查看视频弹幕。

    BV_OR_URL 可以是 BV 号（如 BV1xxx）或完整 URL。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)
    bvid = common.extract_bvid_or_exit(bv_or_url)

    items = common.run_or_exit(
        client.get_video_danmakus(bvid, limit=count, page_index=page_index),
        "获取弹幕失败",
    )

    payload = {
        "bvid": bvid,
        "count": len(items),
        "items": items,
    }
    if common.emit_structured(payload, output_format):
        return

    if not items:
        common.console.print("[yellow]该视频没有弹幕[/yellow]")
        return

    for d in items:
        stamp = common.format_duration(round(d.get("time", 0)))
        common.console.print(f"[dim]{stamp}[/dim]  {d.get('text', '')}")
