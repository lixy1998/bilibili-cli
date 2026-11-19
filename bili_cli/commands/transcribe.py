"""Transcription command — subtitle first, local Whisper as fallback."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

import click

from . import common


def _srt_time(seconds: float) -> str:
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = seconds % 60
    return f"{hours:02d}:{minutes:02d}:{secs:06.3f}".replace(".", ",")


def _to_srt(segments: list[dict]) -> str:
    lines: list[str] = []
    for index, seg in enumerate(segments, 1):
        lines.append(str(index))
        lines.append(f"{_srt_time(seg['start'])} --> {_srt_time(seg['end'])}")
        lines.append(seg["text"])
        lines.append("")
    return "\n".join(lines)


@click.command()
@click.argument("bv_or_url")
@click.option(
    "--model",
    default="large-v3-turbo",
    show_default=True,
    help="faster-whisper 模型（tiny / base / small / medium / large-v3-turbo）。",
)
@click.option("--language", default="zh", show_default=True, help="音频语言。")
@click.option("--format", "fmt", type=click.Choice(["text", "srt"]), default="text", show_default=True, help="输出格式。")
@click.option("--with-segments", is_flag=True, help="结构化输出附带带时间戳的分段。")
@click.option("--no-subtitle", is_flag=True, help="跳过已有字幕，强制本地转写。")
@click.option("-o", "--output", "output_path", default=None, help="写入文件（默认打印）。")
@common.structured_output_options
def transcribe(
    bv_or_url: str,
    model: str,
    language: str,
    fmt: str,
    with_segments: bool,
    no_subtitle: bool,
    output_path: str | None,
    as_json: bool,
    as_yaml: bool,
):
    """获取视频文字稿：优先用 B站字幕，没有字幕就本地转写（faster-whisper）。

    转写需要：pip install bilibili-cli[asr]
    """
    from .. import client

    output_format = common.resolve_output_format(as_json=as_json, as_yaml=as_yaml)
    bvid = common.extract_bvid_or_exit(bv_or_url)
    cred = common.get_credential(mode="optional")

    text = ""
    source = ""
    segments: list[dict] = []
    subtitle_reason = ""

    if not no_subtitle:
        detail = common.run_optional(
            client.get_video_subtitle_detailed(bvid, credential=cred), "获取字幕失败"
        )
        if isinstance(detail, dict):
            subtitle_reason = detail.get("reason", "")
            if detail.get("available"):
                text = detail.get("text", "")
                source = "subtitle"

    if not text:
        try:
            from faster_whisper import WhisperModel
        except ImportError:
            common.exit_error(
                "本地转写需要 faster-whisper：pip install bilibili-cli[asr]"
                f"（当前字幕不可用: {subtitle_reason or 'unknown'}）",
                code="missing_dependency",
            )

        workdir = Path(tempfile.mkdtemp(prefix="bili-asr-"))
        try:
            audio_url = common.run_or_exit(
                client.get_audio_url(bvid, credential=cred), "获取音频地址失败"
            )
            audio_path = workdir / f"{bvid}.m4a"
            common.console.print("[dim]⬇️  下载音频…[/dim]")
            common.run_or_exit(
                client.download_audio(audio_url, str(audio_path)), "下载音频失败"
            )

            common.console.print(f"[dim]🎙  本地转写中（{model}，首次会下载模型）…[/dim]")
            try:
                whisper = WhisperModel(model, device="cpu", compute_type="int8")
                iterator, info = whisper.transcribe(
                    str(audio_path),
                    language=language,
                    vad_filter=True,
                    beam_size=5,
                    initial_prompt="以下是普通话讲课的句子。",
                )
                for seg in iterator:
                    piece = (seg.text or "").strip()
                    if not piece:
                        continue
                    segments.append(
                        {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": piece}
                    )
            except Exception as e:  # noqa: BLE001 — 模型缺失/下载失败都归到这里
                common.exit_error(
                    "本地转写不可用: "
                    f"{e}\n可试：① 指定本地模型目录 --model <dir>；"
                    "② 设置镜像 HF_ENDPOINT=https://hf-mirror.com 后重试；"
                    "③ 先用 bili audio 导出音频交给外部 ASR。",
                    code="asr_unavailable",
                )
            text = "\n".join(s["text"] for s in segments)
            source = "asr"
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

    if not text:
        common.exit_error("转写结果为空（可能视频没有语音）", code="no_content")

    payload: dict[str, object] = {
        "bvid": bvid,
        "source": source,
        "model": None if source == "subtitle" else model,
        "language": language,
        "chars": len(text),
        "text": text,
    }
    if with_segments and segments:
        payload["segments"] = segments

    if common.emit_structured(payload, output_format):
        return

    body = _to_srt(segments) if (fmt == "srt" and segments) else text

    if output_path:
        path = Path(output_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
        common.console.print(f"[green]✅ 已写入 {path}（来源: {source}）[/green]")
        return

    click.echo(body)
