"""Video related command."""

from __future__ import annotations

import sys

import click
from rich.table import Table

from .. import payloads
from . import common

_SUBTITLE_FAILURE_REASONS = {"not_authenticated", "subtitle_unavailable", "download_failed"}


def _split_tokens(raw: str) -> list[str]:
    """Split free-form input on whitespace and (full-width) commas."""
    normalized = raw.replace(",", " ").replace("，", " ").replace("\n", " ")
    return [chunk.strip() for chunk in normalized.split() if chunk.strip()]


def _pubdate_label(item: dict) -> str:
    """Render a publish timestamp as a short date."""
    from datetime import datetime

    ts = item.get("pubdate") or item.get("senddate") or 0
    try:
        return datetime.fromtimestamp(int(ts)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


@click.command()
@click.argument("bv_or_url", nargs=-1, required=False)
@click.option(
    "--stdin",
    "read_stdin",
    is_flag=True,
    help="从标准输入读取 BV 号（空白或换行分隔），可与参数并用。",
)
@click.option("--subtitle", "-s", is_flag=True, help="显示字幕内容。")
@click.option("--subtitle-timeline", "-st", is_flag=True, help="显示带时间线的字幕。")
@click.option(
    "--subtitle-format",
    type=click.Choice(["timeline", "srt"]),
    default="timeline",
    help="字幕格式：timeline 或 srt。",
)
@click.option("--comments", "-c", is_flag=True, help="显示评论。")
@click.option("--ai", is_flag=True, help="显示 AI 总结。")
@click.option("--related", "-r", is_flag=True, help="显示相关推荐视频。")
@common.structured_output_options
def video(
    bv_or_url: tuple[str, ...],
    read_stdin: bool,
    subtitle: bool,
    subtitle_timeline: bool,
    subtitle_format: str,
    comments: bool,
    ai: bool,
    related: bool,
    as_json: bool,
    as_yaml: bool,
):
    """查看视频详情（支持一次传入多个 BV 号或 URL）。

    BV_OR_URL 可以是 BV 号（如 BV1xxx）、完整 URL，或空格/逗号分隔的多个。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    tokens: list[str] = []
    for arg in bv_or_url:
        tokens.extend(_split_tokens(arg))
    if read_stdin:
        tokens.extend(_split_tokens(sys.stdin.read()))
    if not tokens:
        common.exit_error(
            "未提供 BV 号：bili video BV1xxx，或 bili video --stdin < bvs.txt",
            code="invalid_input",
        )

    bvids: list[str] = []
    for token in tokens:
        bvid = common.extract_bvid_or_exit(token)
        if bvid not in bvids:
            bvids.append(bvid)

    multi = len(bvids) > 1
    needs_optional_cred = subtitle or subtitle_timeline or comments or ai or related
    cred = common.get_credential(mode="optional") if needs_optional_cred else None

    collected: list[dict] = []
    failed_reasons: list[tuple[str, str]] = []

    for position, bvid in enumerate(bvids, 1):
        info = common.run_or_exit(
            client.get_video_info(bvid, credential=None),
            "获取视频信息失败",
        )

        subtitle_text = ""
        subtitle_items: list[dict] = []
        subtitle_reason = ""
        ai_summary = ""
        comments_items: list[dict] = []
        related_items: list[dict] = []
        warnings: list[dict[str, str]] = []
        subtitle_failed = False

        if subtitle or subtitle_timeline:
            try:
                subtitle_text, subtitle_items = common.run(
                    client.get_video_subtitle(bvid, credential=cred)
                )
                subtitle_reason = (
                    "ok" if (subtitle_text or subtitle_items) else "no_subtitle_track"
                )
            except Exception as e:  # noqa: BLE001 — 需要把鉴权失败与"无字幕"区分开
                subtitle_failed = True
                subtitle_reason = (
                    "not_authenticated" if cred is None else "subtitle_unavailable"
                )
                warnings.append(
                    {"code": subtitle_reason, "message": f"获取字幕失败: {e}"}
                )
                failed_reasons.append((bvid, subtitle_reason))

        subtitle_message = warnings[0]["message"] if subtitle_failed and warnings else ""

        if ai:
            ai_data = common.run_optional(
                client.get_video_ai_conclusion(bvid, credential=cred),
                "获取 AI 总结失败",
            )
            if ai_data is not None:
                ai_summary = ai_data.get("model_result", {}).get("summary", "")
            else:
                warnings.append({"code": "ai_summary_unavailable", "message": "获取 AI 总结失败"})

        if comments:
            cm_data = common.run_optional(
                client.get_video_comments(bvid, credential=cred),
                "获取评论失败",
            )
            if cm_data is not None:
                comments_items = cm_data.get("replies") or []
            else:
                warnings.append({"code": "comments_unavailable", "message": "获取评论失败"})

        if related:
            rel_list = common.run_optional(
                client.get_related_videos(bvid, credential=cred),
                "获取相关推荐失败",
            )
            if rel_list is not None:
                related_items = rel_list
            else:
                warnings.append({"code": "related_unavailable", "message": "获取相关推荐失败"})

        structured_payload = payloads.normalize_video_command_payload(
            info,
            subtitle_text=subtitle_text,
            subtitle_items=subtitle_items,
            subtitle_format=subtitle_format if subtitle_timeline else "plain",
            subtitle_reason=subtitle_reason,
            ai_summary=ai_summary,
            comments=comments_items,
            related=related_items,
            warnings=warnings,
        )
        collected.append(structured_payload)

        if output_format:
            continue

        _render_rich(
            info=info,
            bvid=bvid,
            subtitle=subtitle or subtitle_timeline,
            subtitle_timeline=subtitle_timeline,
            subtitle_format=subtitle_format,
            subtitle_text=subtitle_text,
            subtitle_items=subtitle_items,
            subtitle_reason=subtitle_reason,
            subtitle_failed=subtitle_failed,
            subtitle_message=subtitle_message,
            ai=ai,
            ai_summary=ai_summary,
            comments=comments,
            comments_items=comments_items,
            related=related,
            related_items=related_items,
            header=f"[{position}/{len(bvids)}] " if multi else "",
        )

    if output_format:
        if multi:
            payload = {"items": collected, "meta": {"count": len(bvids)}}
        else:
            payload = collected[0]

        if failed_reasons:
            bvid, reason = failed_reasons[0]
            payload = common.partial_failure_payload(
                payload,
                code=reason,
                message=f"{bvid} 的字幕未能获取（{reason}）；详情部分仍可用",
            )
        if common.emit_structured(payload, output_format):
            if failed_reasons:
                sys.exit(2)
            return

    if failed_reasons:
        sys.exit(2)


def _render_rich(
    *,
    info: dict,
    bvid: str,
    subtitle: bool,
    subtitle_timeline: bool,
    subtitle_format: str,
    subtitle_text: str,
    subtitle_items: list[dict],
    subtitle_reason: str,
    subtitle_failed: bool,
    subtitle_message: str,
    ai: bool,
    ai_summary: str,
    comments: bool,
    comments_items: list[dict],
    related: bool,
    related_items: list[dict],
    header: str = "",
) -> None:
    """Render one video as a human-readable block."""
    from .. import client

    stat = info.get("stat", {})
    owner = info.get("owner", {})

    table = Table(
        title=f"{header}📺 {info.get('title', bvid)}",
        show_header=False,
        border_style="blue",
    )
    table.add_column("Field", style="bold cyan", width=12)
    table.add_column("Value")

    table.add_row("BV号", bvid)
    table.add_row("标题", info.get("title", ""))
    table.add_row("UP主", f"{owner.get('name', '')}  (UID: {owner.get('mid', '')})")
    published = _pubdate_label(info)
    if published:
        table.add_row("发布", published)
    table.add_row("时长", common.format_duration(info.get("duration", 0)))
    table.add_row("播放", common.format_count(stat.get("view", 0)))
    table.add_row("弹幕", common.format_count(stat.get("danmaku", 0)))
    table.add_row("点赞", common.format_count(stat.get("like", 0)))
    table.add_row("投币", common.format_count(stat.get("coin", 0)))
    table.add_row("收藏", common.format_count(stat.get("favorite", 0)))
    table.add_row("分享", common.format_count(stat.get("share", 0)))
    table.add_row("链接", f"https://www.bilibili.com/video/{bvid}")

    desc = (info.get("desc") or "").strip()
    if desc:
        table.add_row("简介", desc[:200])

    common.console.print(table)

    if subtitle:
        common.console.print("\n[bold]📝 字幕内容:[/bold]\n")
        if subtitle_timeline and subtitle_items:
            display_content = client.format_subtitle_timeline(
                subtitle_items, output_format=subtitle_format
            )
        else:
            display_content = subtitle_text

        if display_content:
            common.console.print(display_content)
        elif subtitle_failed and subtitle_reason == "not_authenticated":
            if subtitle_message:
                common.console.print(f"[yellow]⚠️  {subtitle_message}[/yellow]")
            common.console.print(
                "[yellow]⚠️  字幕需要登录态：先 bili login。"
                "未登录时无法区分「视频没有字幕」与「拿不到字幕」[/yellow]"
            )
        elif subtitle_failed:
            common.console.print(
                f"[yellow]⚠️  获取字幕失败（{subtitle_reason}）[/yellow]"
            )
        else:
            common.console.print("[yellow]⚠️  该视频没有字幕轨[/yellow]")

    if ai:
        common.console.print("\n[bold]🤖 AI 总结:[/bold]\n")
        if ai_summary:
            common.console.print(ai_summary)
        else:
            common.console.print("[yellow]⚠️  该视频暂无 AI 总结[/yellow]")

    if comments:
        common.console.print("\n[bold]💬 热门评论:[/bold]\n")
        if not comments_items:
            common.console.print("[yellow]暂无评论[/yellow]")
        else:
            for c in comments_items[:10]:
                member = c.get("member", {})
                content = c.get("content", {}).get("message", "")
                likes = c.get("like", 0)
                uname = member.get("uname", "")
                common.console.print(f"  [cyan]{uname}[/cyan]  [dim](👍 {likes})[/dim]")
                common.console.print(f"  {content[:120]}")
                common.console.print()

    if related:
        common.console.print()
        if related_items:
            rel_table = Table(title="📎 相关推荐", border_style="blue")
            rel_table.add_column("#", style="dim", width=4)
            rel_table.add_column("BV号", style="cyan", width=14)
            rel_table.add_column("标题", max_width=40)
            rel_table.add_column("UP主", width=12)
            rel_table.add_column("播放", width=8, justify="right")

            for i, rv in enumerate(related_items[:10], 1):
                ro = rv.get("owner", {})
                rs = rv.get("stat", {})
                rel_table.add_row(
                    str(i),
                    rv.get("bvid", ""),
                    rv.get("title", "")[:40],
                    ro.get("name", "")[:12],
                    common.format_count(rs.get("view", 0)),
                )
            common.console.print(rel_table)
