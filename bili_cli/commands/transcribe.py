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


def _run_whisper(model_obj, audio_path: str, *, language: str, vad: bool) -> list[dict]:
    """Transcribe one audio file and normalise the segments."""
    iterator, _info = model_obj.transcribe(
        audio_path,
        language=language,
        vad_filter=vad,
        beam_size=5,
        initial_prompt="以下是普通话讲课的句子。",
    )
    out: list[dict] = []
    for seg in iterator:
        piece = (seg.text or "").strip()
        if not piece:
            continue
        out.append(
            {"start": round(seg.start, 2), "end": round(seg.end, 2), "text": piece}
        )
    return out


def _resolve_model(model: str | None) -> str:
    """Pick a usable local model before falling back to a Hub repo name.

    Layout: <cli home>/models/<model name>/{model.bin,config.json,...}
    """
    if model:
        return model

    from .. import ledger

    root = ledger.cli_home() / "models"
    for name in (
        "faster-whisper-large-v3-turbo",
        "faster-whisper-medium",
        "faster-whisper-small",
        "faster-whisper-base",
        "faster-whisper-tiny",
    ):
        candidate = root / name
        if (candidate / "model.bin").exists():
            return str(candidate)

    return "large-v3-turbo"


@click.command()
@click.argument("bv_or_url")
@click.option(
    "--model",
    default=None,
    help="faster-whisper 模型名或本地目录（默认自动使用 ~/.bilibili-cli/models 下的可用模型）。",
)
@click.option("--language", default="zh", show_default=True, help="音频语言。")
@click.option("--format", "fmt", type=click.Choice(["text", "srt"]), default="text", show_default=True, help="输出格式。")
@click.option("--with-segments", is_flag=True, help="结构化输出附带带时间戳的分段。")
@click.option("--no-subtitle", is_flag=True, help="跳过已有字幕，强制本地转写。")
@click.option(
    "--vad/--no-vad",
    "vad",
    default=True,
    show_default=True,
    help="先用 VAD 过滤静音；若整段检测不到语音会自动关掉重试一次。",
)
@click.option("-o", "--output", "output_path", default=None, help="写入文件（默认打印）。")
@common.structured_output_options
def transcribe(
    bv_or_url: str,
    model: str | None,
    language: str,
    fmt: str,
    with_segments: bool,
    no_subtitle: bool,
    vad: bool,
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
    model = _resolve_model(model)

    text = ""
    source = ""
    segments: list[dict] = []
    subtitle_reason = ""
    vad_fallback = False

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
            vad_fallback = False
            try:
                whisper = WhisperModel(model, device="cpu", compute_type="int8")
                segments = _run_whisper(
                    whisper, str(audio_path), language=language, vad=vad
                )
                # 小模型/短音频下 VAD 可能把整段判成静音，关掉再来一次
                if not segments and vad:
                    common.console.print("[dim]VAD 未检出语音，关闭 VAD 重试…[/dim]")
                    segments = _run_whisper(
                        whisper, str(audio_path), language=language, vad=False
                    )
                    vad_fallback = True
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
        "vad": vad if source == "asr" else None,
        "vad_fallback": vad_fallback,
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
