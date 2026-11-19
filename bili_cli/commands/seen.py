"""Local seen ledger commands (cross-run dedup)."""

from __future__ import annotations

import click

from .. import ledger
from . import common


@click.group(name="seen")
def seen():
    """本地「看过的视频」账本（跨轮去重）。

    记录保存在 ~/.bilibili-cli/seen.jsonl（可用 BILI_CLI_HOME 覆盖目录）。
    """


@seen.command("list")
@click.option("--max", "-n", "count", default=20, type=click.IntRange(1), help="显示最近几条 (默认 20)。")
@common.structured_output_options
def seen_list(count: int, as_json: bool, as_yaml: bool):
    """列出记录过的视频（最近优先）。"""
    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    records = sorted(
        ledger.load().values(), key=lambda r: r.get("at", ""), reverse=True
    )[:count]

    if common.emit_structured(
        {"path": str(ledger.ledger_path()), "count": len(records), "items": records},
        output_format,
    ):
        return

    if not records:
        common.console.print("[yellow]账本还是空的（用 crawl --mark-seen 或 seen add 记一笔）[/yellow]")
        return

    for record in records:
        common.console.print(
            f"[cyan]{record.get('bvid', '')}[/cyan]  "
            f"[dim]{record.get('at', '')}[/dim]  "
            f"{record.get('title', '')[:40]}"
        )


@seen.command("add")
@click.argument("bvids", nargs=-1, required=True)
@click.option("--note", default="", help="附一条备注。")
@common.structured_output_options
def seen_add(bvids: tuple[str, ...], note: str, as_json: bool, as_yaml: bool):
    """把 BV 号记进账本（已存在的会跳过）。"""
    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    entries = []
    for raw in bvids:
        bvid = common.extract_bvid_or_exit(raw)
        entries.append({"bvid": bvid, "note": note})

    added = ledger.mark(entries)

    if common.emit_structured(
        {
            "added": added,
            "skipped": len(entries) - added,
            "path": str(ledger.ledger_path()),
        },
        output_format,
    ):
        return

    common.console.print(f"[green]✅ 新增 {added} 条，跳过 {len(entries) - added} 条[/green]")


@seen.command("check")
@click.argument("bvids", nargs=-1, required=True)
@common.structured_output_options
def seen_check(bvids: tuple[str, ...], as_json: bool, as_yaml: bool):
    """检查这些 BV 号是否已在账本里。"""
    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    known = ledger.load()
    results = []
    for raw in bvids:
        bvid = common.extract_bvid_or_exit(raw)
        record = known.get(bvid)
        results.append(
            {
                "bvid": bvid,
                "seen": record is not None,
                "at": (record or {}).get("at", ""),
                "title": (record or {}).get("title", ""),
            }
        )

    if common.emit_structured({"items": results}, output_format):
        return

    for item in results:
        mark = "[yellow]看过[/yellow]" if item["seen"] else "[green]没看过[/green]"
        common.console.print(f"{item['bvid']}  {mark}  {item.get('at', '')}")


@seen.command("clear")
@click.option("--yes", "confirmed", is_flag=True, help="确认删除整个账本。")
@common.structured_output_options
def seen_clear(confirmed: bool, as_json: bool, as_yaml: bool):
    """清空账本（需 --yes 确认）。"""
    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)

    if not confirmed:
        common.exit_error("清空账本需要 --yes 确认", code="invalid_input")

    removed = ledger.clear()

    if common.emit_structured({"removed": removed}, output_format):
        return

    common.console.print(f"[green]✅ 已删除 {removed} 条记录[/green]")
