"""Search + subtitle in one step (the "crawl" command)."""

from __future__ import annotations

import time

import click

from .. import ledger, payloads
from . import common
from .user_search import DURATION_CHOICES, VIDEO_ORDER_CHOICES

_UNKNOWN_SUBTITLE = {
    "available": False,
    "reason": "error",
    "message": "",
    "text": "",
    "items": [],
}


def _render_markdown(
    keyword: str,
    entries: list[dict],
    meta: dict,
    missing: int,
) -> str:
    """Render one search round as agent-readable Markdown."""
    lines: list[str] = [f"# {keyword} · 搜索汇总", ""]
    window = f"{meta.get('since') or '不限'} ~ {meta.get('until') or '今天'}"
    lines.append(
        f"- 抓取 {len(entries)} 条（请求 {meta.get('requested')} 条，翻 {meta.get('pages_fetched')} 页）"
        f"｜排序 {meta.get('order')}｜时间 {window}"
    )
    if not meta.get("with_subtitle", True):
        lines.append("- 本次未抓字幕（--no-subtitle）")
    elif missing:
        lines.append(f"- 无字幕 / 取不到字幕 {missing} 条（原因见各条）")
    lines.append("")

    for entry in entries:
        video = entry["video"]
        subtitle = entry["subtitle"]
        stats = video.get("stats", {})
        lines.append(f"### {video['bvid']}")
        lines.append("")
        lines.append(f"- 标题：{video['title']}")
        lines.append(f"- UP：{video['author']}（UID {video.get('up', {}).get('id', '')}）")
        lines.append(f"- 链接：{video['url']}")
        lines.append(
            f"- 发布：{video.get('published_at') or '未知'}"
            f"｜播放 {stats.get('view', 0)}"
            f"｜收藏 {stats.get('favorite', 0)}"
            f"｜评论 {stats.get('review', 0)}"
            f"｜时长 {video.get('duration', '')}"
        )
        if subtitle.get("available"):
            lines.append(f"- 字幕：{subtitle.get('chars', 0)} 字")
            lines.append("")
            lines.append("```")
            lines.append(subtitle.get("text", ""))
            lines.append("```")
        else:
            lines.append(f"- 字幕：无（{subtitle.get('reason') or 'unknown'}）")
        lines.append("")

    return "\n".join(lines)


@click.command()
@click.argument("keyword")
@click.option("--max", "-n", "count", default=5, type=click.IntRange(1), help="最多抓几条 (默认 5)。")
@click.option(
    "--order",
    type=click.Choice(VIDEO_ORDER_CHOICES),
    default="pubdate",
    show_default=True,
    help="搜索排序（默认 pubdate 最新）。",
)
@click.option("--since", default=None, help="起始日期：YYYY-MM-DD 或 7d / 30d / today。")
@click.option("--until", default=None, help="结束日期：YYYY-MM-DD 或 today。")
@click.option("--tid", type=int, default=None, help="分区 id。")
@click.option("--duration", type=click.Choice(DURATION_CHOICES), default=None, help="时长筛选（分钟档位）。")
@click.option(
    "--subtitle-chars",
    "subtitle_chars",
    default=3000,
    type=click.IntRange(0),
    help="每条字幕保留的最大字符数 (默认 3000，0 = 不限)。",
)
@click.option("--no-subtitle", is_flag=True, help="只出搜索结果，不抓字幕。")
@click.option("--exclude-seen", is_flag=True, help="跳过本地账本里已记录的视频。")
@click.option("--mark-seen", is_flag=True, help="抓完后把结果记入本地账本（下次 --exclude-seen 就不重复）。")
@click.option("-o", "--output", "output_path", default=None, help="写入 Markdown 文件（默认打印到标准输出）。")
@common.structured_output_options
def crawl(
    keyword: str,
    count: int,
    order: str,
    since: str | None,
    until: str | None,
    tid: int | None,
    duration: str | None,
    subtitle_chars: int,
    no_subtitle: bool,
    exclude_seen: bool,
    mark_seen: bool,
    output_path: str | None,
    as_json: bool,
    as_yaml: bool,
):
    """搜索关键词并抓取前 N 条的视频字幕，一步出可读汇总。

    KEYWORD 是要搜的关键词。默认按最新发布排序，便于只看近期内容。
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    since_iso = common.resolve_date(since, param="--since")
    until_iso = common.resolve_date(until, param="--until")
    if since_iso and until_iso and since_iso > until_iso:
        common.exit_error("--since 不能晚于 --until", code="invalid_input")

    extras: dict[str, object] = {"order": order}
    if since_iso:
        extras["time_start"] = since_iso
    if until_iso:
        extras["time_end"] = until_iso
    if tid is not None:
        extras["tid"] = tid
    if duration:
        extras["duration"] = duration

    if exclude_seen:
        known = set(ledger.load())
        if known:
            extras["exclude_bvids"] = known

    search_result = common.run_or_exit(
        client.search_video_paged(keyword, count=count, **extras),
        "搜索视频失败",
    )
    videos = [v for v in search_result["items"] if v.get("bvid")]
    skipped_seen = search_result["meta"].get("skipped_seen", 0)
    if skipped_seen:
        common.console.print(
            f"[dim]已跳过 {skipped_seen} 条账本里看过的视频（继续翻页补足）[/dim]"
        )

    cred = None
    if not no_subtitle:
        cred = common.get_credential(mode="optional")

    entries: list[dict] = []
    missing = 0

    for video in videos:
        bvid = video["bvid"]
        entry: dict[str, object] = {
            "video": payloads.normalize_search_video(video, full=True),
            "subtitle": {"available": False, "reason": "skipped", "chars": 0, "text": ""},
        }

        if not no_subtitle:
            detail = common.run_optional(
                client.get_video_subtitle_detailed(bvid, credential=cred),
                f"获取字幕失败（{bvid}）",
            )
            if not isinstance(detail, dict):
                detail = dict(_UNKNOWN_SUBTITLE)

            full_text = detail.get("text") or ""
            shown = full_text
            if subtitle_chars and len(shown) > subtitle_chars:
                shown = shown[:subtitle_chars] + "\n…（已截断）"

            entry["subtitle"] = {
                "available": bool(detail.get("available")),
                "reason": detail.get("reason"),
                "message": detail.get("message", ""),
                "chars": len(full_text),
                "text": shown,
            }
            if not detail.get("available"):
                missing += 1
            time.sleep(0.2)

        entries.append(entry)

    meta = {
        **search_result["meta"],
        "with_subtitle": not no_subtitle,
        "missing_subtitle": missing,
        "returned": len(entries),
        "skipped_seen": skipped_seen,
    }

    if mark_seen and entries:
        added = ledger.mark(
            [
                {
                    "bvid": e["video"]["bvid"],
                    "title": e["video"]["title"],
                    "author": e["video"]["author"],
                    "note": keyword,
                }
                for e in entries
            ]
        )
        meta["marked_seen"] = added

    payload = {
        "keyword": keyword,
        "meta": meta,
        "items": entries,
    }

    if common.emit_structured(payload, output_format):
        return

    markdown = _render_markdown(keyword, entries, meta, missing)

    if output_path:
        from pathlib import Path

        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(markdown, encoding="utf-8")
        common.console.print(f"[green]✅ 已写入 {path}[/green]")
        return

    click.echo(markdown)
