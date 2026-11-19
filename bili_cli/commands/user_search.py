"""User and search related commands."""

from __future__ import annotations

import re
from datetime import datetime

import click
from rich.panel import Panel
from rich.table import Table

from .. import payloads
from . import common

VIDEO_ORDER_CHOICES = ["totalrank", "click", "pubdate", "dm", "stow", "scores"]
DURATION_CHOICES = ["lt10", "10-30", "30-60", "gt60"]


def _pubdate_label(item: dict) -> str:
    """Render a publish timestamp as a short date for the table view."""
    ts = item.get("pubdate") or item.get("senddate") or 0
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _resolve_uid(uid_or_name: str) -> int:
    """Resolve a UID or username to a numeric UID."""
    from .. import client

    if uid_or_name.isdigit():
        return int(uid_or_name)

    results = common.run_or_exit(client.search_user(uid_or_name), "搜索用户失败")
    if not results:
        common.exit_error(f"未找到用户: {uid_or_name}", code="not_found")

    uid_raw = results[0].get("mid")
    if uid_raw is None:
        common.exit_error(f"搜索结果缺少 UID: {uid_or_name}", code="upstream_error")

    try:
        uid = int(uid_raw)
    except (TypeError, ValueError):
        common.exit_error(f"搜索结果 UID 非法: {uid_raw}", code="upstream_error")

    common.console.print(f"[dim]🔍 匹配到: {results[0].get('uname', '')} (UID: {uid})[/dim]\n")
    return uid


def _format_video_length(length_raw: object) -> str:
    """Normalize different video length payloads into display string."""
    if isinstance(length_raw, str):
        if ":" in length_raw:
            return length_raw
        if not length_raw:
            return "00:00"
        try:
            return common.format_duration(int(length_raw))
        except ValueError:
            return "00:00"

    if isinstance(length_raw, int):
        return common.format_duration(length_raw)

    return "00:00"


@click.command()
@click.argument("uid_or_name")
@common.structured_output_options
def user(uid_or_name: str, as_json: bool, as_yaml: bool):
    """查看 UP 主资料。

    UID_OR_NAME 可以是 UID（纯数字）或用户名（搜索第一个匹配）。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    uid = _resolve_uid(uid_or_name)

    info = common.run_or_exit(client.get_user_info(uid, credential=None), "获取用户信息失败")
    relation = common.run_or_exit(
        client.get_user_relation_info(uid, credential=None),
        "获取用户信息失败",
    )

    structured = {
        "user": payloads.normalize_user(info),
        "relation": payloads.normalize_relation(relation),
    }
    if common.emit_structured(structured, output_format):
        return

    follower = relation.get("follower", 0)
    following = relation.get("following", 0)

    common.console.print(Panel(
        f"👤 [bold]{info.get('name', '')}[/bold]  (UID: {uid})\n"
        f"⭐ Level {info.get('level', '?')}  |  "
        f"👥 粉丝 {common.format_count(follower)}  |  "
        f"🔔 关注 {common.format_count(following)}",
        title="UP 主信息",
        border_style="cyan",
    ))

    sign = info.get("sign", "").strip()
    if sign:
        common.console.print(f"[dim]{sign}[/dim]")


@click.command(name="user-videos")
@click.argument("uid_or_name")
@click.option("--max", "-n", "count", default=10, type=click.IntRange(1), help="显示的视频数量 (默认 10，最小 1)。")
@common.structured_output_options
def user_videos(uid_or_name: str, count: int, as_json: bool, as_yaml: bool):
    """查看 UP 主的视频列表。

    UID_OR_NAME 可以是 UID（纯数字）或用户名（搜索第一个匹配）。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    uid = _resolve_uid(uid_or_name)

    videos = common.run_or_exit(
        client.get_user_videos(uid, count=count, credential=None),
        "获取视频列表失败",
    )

    if common.emit_structured([payloads.normalize_video_summary(video) for video in videos], output_format):
        return

    if not videos:
        common.console.print("[yellow]该用户暂无视频[/yellow]")
        return

    table = Table(title=f"最新 {len(videos)} 个视频", border_style="blue")
    table.add_column("#", style="dim", width=4)
    table.add_column("BV号", style="cyan", width=14)
    table.add_column("标题", max_width=40)
    table.add_column("时长", width=8)
    table.add_column("播放", width=8, justify="right")

    for i, v in enumerate(videos, 1):
        length_str = _format_video_length(v.get("length", "0"))
        table.add_row(
            str(i),
            v.get("bvid", ""),
            v.get("title", "")[:40],
            length_str,
            common.format_count(v.get("play", 0)),
        )

    common.console.print(table)


@click.command()
@click.argument("keyword")
@click.option("--type", "search_type", default="user", type=click.Choice(["user", "video"]), help="搜索类型 (默认 user)。")
@click.option("--page", default=1, type=click.IntRange(1), help="页码 (默认 1，最小 1)。")
@click.option("--max", "-n", "count", default=20, type=click.IntRange(1), help="显示数量 (默认 20，最小 1；视频搜索会自动翻页凑够)。")
@click.option(
    "--order",
    type=click.Choice(VIDEO_ORDER_CHOICES),
    default=None,
    help="视频排序：totalrank 综合 / click 最多播放 / pubdate 最新 / dm 最多弹幕 / stow 最多收藏 / scores 最多评论。",
)
@click.option("--since", default=None, help="起始日期：YYYY-MM-DD 或 7d / 30d / today。")
@click.option("--until", default=None, help="结束日期：YYYY-MM-DD 或 today。")
@click.option(
    "--duration",
    type=click.Choice(DURATION_CHOICES),
    default=None,
    help="时长筛选（分钟）：lt10 / 10-30 / 30-60 / gt60。",
)
@click.option("--tid", type=int, default=None, help="分区 id（如 188 科技、科技数码等）。")
@click.option(
    "--fields",
    "fields",
    type=click.Choice(["basic", "full"]),
    default="basic",
    help="输出字段：basic 精简（6 字段）/ full 含发布时间、收藏、评论数、封面、标签。",
)
@click.option("--meta", "with_meta", is_flag=True, help="结构化输出附带 meta（翻页数、是否截断）。")
@click.option("--exclude-seen", is_flag=True, help="跳过本地账本里已记录的视频（见 bili seen）。")
@common.structured_output_options
def search(
    keyword: str,
    search_type: str,
    page: int,
    count: int,
    order: str | None,
    since: str | None,
    until: str | None,
    duration: str | None,
    tid: int | None,
    fields: str,
    with_meta: bool,
    exclude_seen: bool,
    as_json: bool,
    as_yaml: bool,
):
    """搜索用户或视频。"""
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    if search_type == "video":
        since_iso = common.resolve_date(since, param="--since")
        until_iso = common.resolve_date(until, param="--until")
        if since_iso and until_iso and since_iso > until_iso:
            common.exit_error("--since 不能晚于 --until", code="invalid_input")

        extras: dict[str, object] = {}
        if order:
            extras["order"] = order
        if since_iso:
            extras["time_start"] = since_iso
        if until_iso:
            extras["time_end"] = until_iso
        if duration:
            extras["duration"] = duration
        if tid is not None:
            extras["tid"] = tid

        if exclude_seen:
            from .. import ledger

            known = set(ledger.load())
            if known:
                extras["exclude_bvids"] = known

        result = common.run_or_exit(
            client.search_video_paged(keyword, count=count, page=page, **extras),
            "搜索视频失败",
        )
        meta = result["meta"]
        full = fields == "full"
        if meta.get("skipped_seen"):
            common.console.print(
                f"[dim]已跳过 {meta['skipped_seen']} 条账本里看过的视频（继续翻页补足）[/dim]"
            )

        display_results = [v for v in result["items"] if v.get("bvid")]

        if meta.get("truncated"):
            common.console.print(
                f"[yellow]⚠️  只取到 {meta['returned']}/{meta['requested']} 条"
                f"（翻 {meta['pages_fetched']} 页后无更多结果）[/yellow]"
            )
        if meta.get("hit_page_cap"):
            common.console.print(
                f"[yellow]⚠️  已达翻页上限（{meta['pages_fetched']} 页），结果可能不全[/yellow]"
            )

        structured_items = [
            payloads.normalize_search_video(item, full=full) for item in display_results
        ]
        structured = {"items": structured_items, "meta": meta} if with_meta else structured_items
        if common.emit_structured(structured, output_format):
            return

        if not display_results:
            common.console.print(f"[yellow]未找到与 '{keyword}' 相关的视频[/yellow]")
            return

        title_parts = [f"🔍 视频搜索: {keyword}"]
        if order:
            title_parts.append(f"排序 {order}")
        if since_iso or until_iso:
            title_parts.append(f"{since_iso or '不限'} ~ {until_iso or '今天'}")

        table = Table(title=" | ".join(title_parts), border_style="blue")
        table.add_column("#", style="dim", width=4)
        table.add_column("BV号", style="cyan", width=14)
        table.add_column("标题", max_width=36)
        table.add_column("UP主", width=12)
        table.add_column("播放", width=9, justify="right")
        if full:
            table.add_column("收藏", width=8, justify="right")
            table.add_column("发布", width=10)
        table.add_column("时长", width=8)

        for i, v in enumerate(display_results, 1):
            title = re.sub(r'<[^>]+>', '', v.get("title", ""))[:36]
            row = [
                str(i),
                v.get("bvid", ""),
                title,
                v.get("author", "")[:12],
                common.format_count(v.get("play", 0)),
            ]
            if full:
                row.append(common.format_count(v.get("favorites", 0)))
                row.append(_pubdate_label(v))
            row.append(v.get("duration", ""))
            table.add_row(*row)

        common.console.print(table)
    else:
        results = common.run_or_exit(client.search_user(keyword, page=page), "搜索用户失败")

        if common.emit_structured([payloads.normalize_search_user(item) for item in results[:count]], output_format):
            return

        if not results:
            common.console.print(f"[yellow]未找到与 '{keyword}' 相关的用户[/yellow]")
            return

        table = Table(title=f"🔍 搜索: {keyword}", border_style="blue")
        table.add_column("UID", style="cyan", width=12)
        table.add_column("用户名", width=20)
        table.add_column("粉丝", width=10, justify="right")
        table.add_column("视频数", width=8, justify="right")
        table.add_column("签名", max_width=40)

        for u in results[:count]:
            usign = u.get("usign", "")
            table.add_row(
                str(u.get("mid", "")),
                u.get("uname", ""),
                common.format_count(u.get("fans", 0)),
                str(u.get("videos", 0)),
                usign[:40] if usign else "",
            )

        common.console.print(table)
