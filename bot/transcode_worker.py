#!/usr/bin/env python3
# Wardf

import argparse
import fcntl
import json
import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests

CONFIG_FILE = "config.json"
SUPPORTED_MEDIA = {".mkv", ".mp4", ".avi", ".m4v"}

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
)


def load_config() -> Dict[str, Any]:
    with open(CONFIG_FILE, "r", encoding="utf-8") as handle:
        return json.load(handle)


def get_transcode_cfg(cfg: Dict[str, Any]) -> Dict[str, Any]:
    return cfg.get("transcode", {})


def get_queue_path(cfg: Dict[str, Any]) -> Path:
    return Path(get_transcode_cfg(cfg).get("queue_file", "transcode_queue.json"))


def read_queue_unlocked(queue_path: Path) -> List[Dict[str, Any]]:
    try:
        data = json.loads(queue_path.read_text(encoding="utf-8"))
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []


def write_queue_unlocked(queue_path: Path, jobs: List[Dict[str, Any]]) -> None:
    queue_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = queue_path.with_suffix(queue_path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(queue_path)


def update_queue(cfg: Dict[str, Any], callback):
    queue_path = get_queue_path(cfg)
    lock_path = queue_path.with_suffix(queue_path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    with lock_path.open("a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            jobs = read_queue_unlocked(queue_path)
            result = callback(jobs)
            write_queue_unlocked(queue_path, jobs)
            return result
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def claim_next_job(cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    stale_after = int(get_transcode_cfg(cfg).get("processing_stale_after", 21600))
    now = int(time.time())

    def callback(jobs):
        for job in jobs:
            if (
                job.get("status") == "processing"
                and now - int(job.get("started_at", now)) >= stale_after
            ):
                job["status"] = "queued"
                job["last_error"] = "Recovered stale processing job"

        for job in jobs:
            if job.get("status") != "queued":
                continue
            job["status"] = "processing"
            job["started_at"] = now
            job["attempts"] = int(job.get("attempts", 0)) + 1
            return dict(job)
        return None

    return update_queue(cfg, callback)


def finish_job(
    cfg: Dict[str, Any],
    job_id: str,
    status: str,
    error: str = "",
    result: Optional[Dict[str, Any]] = None,
) -> None:
    now = int(time.time())

    def callback(jobs):
        for job in jobs:
            if job.get("job_id") != job_id:
                continue
            job["status"] = status
            job["finished_at"] = now
            job["last_error"] = error
            if result is not None:
                job["result"] = result
            break

        keep_completed = int(get_transcode_cfg(cfg).get("keep_completed_jobs", 100))
        completed = [j for j in jobs if j.get("status") in {"done", "failed"}]
        if len(completed) > keep_completed:
            removable = sorted(
                completed,
                key=lambda item: int(item.get("finished_at", 0)),
            )[: len(completed) - keep_completed]
            remove_ids = {item.get("job_id") for item in removable}
            jobs[:] = [item for item in jobs if item.get("job_id") not in remove_ids]

    update_queue(cfg, callback)


def run_command(command: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    logging.info("Running: %s", " ".join(command))
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def check_binaries(cfg: Dict[str, Any]) -> Tuple[str, str]:
    transcode_cfg = get_transcode_cfg(cfg)
    ffmpeg = str(transcode_cfg.get("ffmpeg_path", "ffmpeg"))
    ffprobe = str(transcode_cfg.get("ffprobe_path", "ffprobe"))

    for executable in (ffmpeg, ffprobe):
        if shutil.which(executable) is None and not Path(executable).is_file():
            raise RuntimeError(f"Required executable not found: {executable}")

    return ffmpeg, ffprobe


def probe_media(ffprobe: str, file_path: Path) -> Dict[str, Any]:
    result = run_command([
        ffprobe,
        "-v", "error",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        "-show_chapters",
        str(file_path),
    ], timeout=120)

    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or "ffprobe failed")

    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"Invalid ffprobe JSON: {error}") from error


def streams_of_type(probe: Dict[str, Any], codec_type: str) -> List[Dict[str, Any]]:
    return [
        stream for stream in probe.get("streams", [])
        if stream.get("codec_type") == codec_type
    ]


def primary_video_codec(probe: Dict[str, Any]) -> str:
    videos = streams_of_type(probe, "video")
    if not videos:
        return ""
    return str(videos[0].get("codec_name", "")).lower()


def duration_seconds(probe: Dict[str, Any]) -> float:
    try:
        return float(probe.get("format", {}).get("duration", 0) or 0)
    except (TypeError, ValueError):
        return 0.0


def human_size(size: int) -> str:
    value = float(size)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def get_series_rule(title: str, cfg: Dict[str, Any]) -> Dict[str, Any]:
    import re

    for pattern, rule in cfg.get("series", {}).items():
        if re.search(pattern, title, re.IGNORECASE):
            output = dict(rule)
            output["_matched_pattern"] = pattern
            return output
    return {}


def stream_title_map(cfg: Dict[str, Any], job: Dict[str, Any], stream_type: str) -> Dict[str, str]:
    transcode_cfg = get_transcode_cfg(cfg)
    global_map = transcode_cfg.get(f"{stream_type}_titles", {})
    rule = get_series_rule(str(job.get("original_title", "")), cfg)
    local_meta = rule.get("stream_metadata", {})
    local_map = local_meta.get(stream_type, {})

    merged = {}
    if isinstance(global_map, dict):
        merged.update({str(k).lower(): str(v) for k, v in global_map.items()})
    if isinstance(local_map, dict):
        merged.update({str(k).lower(): str(v) for k, v in local_map.items()})
    return merged


def metadata_arguments(
    cfg: Dict[str, Any],
    job: Dict[str, Any],
    probe: Dict[str, Any]
) -> List[str]:
    transcode_cfg = get_transcode_cfg(cfg)

    if not transcode_cfg.get("rename_stream_titles", False):
        return []

    arguments: List[str] = []

    # Nazwa ścieżki wideo:
    # 1. Najpierw konfiguracja konkretnej serii.
    # 2. Następnie globalne video_title z sekcji transcode.
    rule = get_series_rule(str(job.get("original_title", "")), cfg)
    local_meta = rule.get("stream_metadata", {})

    video_title = local_meta.get("video_title")
    if not video_title:
        video_title = transcode_cfg.get("video_title")

    if video_title:
        video_title = str(video_title)

        for type_index, stream in enumerate(streams_of_type(probe, "video")):
            tags = stream.get("tags", {}) or {}
            current_title = str(tags.get("title", ""))

            if video_title != current_title:
                arguments.extend([
                    f"-metadata:s:v:{type_index}",
                    f"title={video_title}",
                ])

    # Nazwy ścieżek audio i napisów.
    for codec_type, short_name, config_name in (
        ("audio", "a", "audio"),
        ("subtitle", "s", "subtitles"),
    ):
        title_map = stream_title_map(cfg, job, config_name)

        for type_index, stream in enumerate(
            streams_of_type(probe, codec_type)
        ):
            tags = stream.get("tags", {}) or {}
            language = str(tags.get("language", "und")).lower()
            current_title = str(tags.get("title", ""))
            wanted_title = title_map.get(language)

            if (
                not wanted_title
                and transcode_cfg.get("default_stream_titles", False)
            ):
                wanted_title = (
                    language.upper()
                    if language != "und"
                    else current_title
                )

            if wanted_title and wanted_title != current_title:
                arguments.extend([
                    f"-metadata:s:{short_name}:{type_index}",
                    f"title={wanted_title}",
                ])

    return arguments



def needs_metadata_remux(cfg: Dict[str, Any], job: Dict[str, Any], probe: Dict[str, Any]) -> bool:
    return bool(metadata_arguments(cfg, job, probe))


def webhook_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    specific = cfg.get("discord_transcode_webhook", {})
    if specific.get("url"):
        return specific
    return cfg.get("discord_journal_webhook", {})


def send_transcode_webhook(
    cfg: Dict[str, Any],
    title: str,
    description: str,
    fields: List[Dict[str, Any]],
    color: int,
) -> None:
    webhook_cfg = webhook_config(cfg)
    if not webhook_cfg.get("enabled", False) or not webhook_cfg.get("url"):
        return

    payload = {
        "username": webhook_cfg.get("username", "Nyaa Transcode Worker"),
        "embeds": [{
            "title": title,
            "description": description[:4096],
            "color": color,
            "fields": fields[:25],
            "footer": {"text": "Wardf • Nyaa Transcode Worker"},
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }],
    }

    try:
        response = requests.post(webhook_cfg["url"], json=payload, timeout=20)
        response.raise_for_status()
    except Exception as error:
        logging.warning("Transcode webhook failed: %s", error)


def build_ffmpeg_command(
    cfg: Dict[str, Any],
    job: Dict[str, Any],
    ffmpeg: str,
    source: Path,
    output: Path,
    input_probe: Dict[str, Any],
    transcode_video: bool,
) -> List[str]:
    transcode_cfg = get_transcode_cfg(cfg)
    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i", str(source),
        "-map", "0",
        "-map_metadata", "0",
        "-map_chapters", "0",
        "-c", "copy",
    ]

    if transcode_video:
        encoder = str(transcode_cfg.get("encoder", "libx265"))
        command.extend(["-c:v:0", encoder])

        if encoder == "libx265":
            command.extend([
                "-preset", str(transcode_cfg.get("preset", "medium")),
                "-crf", str(transcode_cfg.get("crf", 22)),
            ])
        elif "nvenc" in encoder:
            command.extend([
                "-preset", str(transcode_cfg.get("preset", "p5")),
                "-cq", str(transcode_cfg.get("cq", 24)),
            ])
        elif "qsv" in encoder:
            command.extend([
                "-global_quality", str(transcode_cfg.get("global_quality", 24)),
            ])

        pixel_format = transcode_cfg.get("pixel_format", "yuv420p10le")
        if pixel_format:
            command.extend(["-pix_fmt", str(pixel_format)])

    command.extend(metadata_arguments(cfg, job, input_probe))
    command.extend(["-max_muxing_queue_size", "4096", str(output)])
    return command


def verify_output(
    source_probe: Dict[str, Any],
    output_probe: Dict[str, Any],
    expect_hevc: bool,
    max_duration_difference: float,
) -> None:
    output_videos = streams_of_type(output_probe, "video")
    if not output_videos:
        raise RuntimeError("Output has no video stream")

    if expect_hevc and primary_video_codec(output_probe) not in {"hevc", "h265"}:
        raise RuntimeError(
            f"Output video codec is {primary_video_codec(output_probe)!r}, not HEVC"
        )

    source_audio = len(streams_of_type(source_probe, "audio"))
    output_audio = len(streams_of_type(output_probe, "audio"))
    if output_audio < source_audio:
        raise RuntimeError(f"Audio tracks lost: {output_audio}/{source_audio}")

    source_subtitles = len(streams_of_type(source_probe, "subtitle"))
    output_subtitles = len(streams_of_type(output_probe, "subtitle"))
    if output_subtitles < source_subtitles:
        raise RuntimeError(
            f"Subtitle tracks lost: {output_subtitles}/{source_subtitles}"
        )

    source_duration = duration_seconds(source_probe)
    output_duration = duration_seconds(output_probe)
    if source_duration and output_duration:
        difference = abs(source_duration - output_duration)
        if difference > max_duration_difference:
            raise RuntimeError(
                f"Duration mismatch: source={source_duration:.3f}s "
                f"output={output_duration:.3f}s"
            )


def safe_replace(
    source: Path,
    output: Path,
    final_path: Path,
    keep_source: bool
) -> None:
    backup = source.with_name(source.name + ".source-backup")
    if backup.exists():
        backup.unlink()

    if final_path != source and final_path.exists():
        raise RuntimeError(f"Final output path already exists: {final_path}")

    source.replace(backup)
    try:
        output.replace(final_path)
        if not keep_source:
            backup.unlink(missing_ok=True)
    except Exception:
        final_path.unlink(missing_ok=True)
        backup.replace(source)
        raise


def process_job(cfg: Dict[str, Any], job: Dict[str, Any]) -> Dict[str, Any]:
    transcode_cfg = get_transcode_cfg(cfg)
    source = Path(str(job.get("file_path", "")))
    rule = get_series_rule(str(job.get("original_title", "")), cfg)

    if not rule.get("transcode", True):
        logging.info(
            "Transcode disabled for series; skipping queued job: %s",
            source
        )
        return {
            "action": "disabled",
            "file_path": str(source),
        }

    ffmpeg, ffprobe = check_binaries(cfg)

    if source.suffix.lower() not in SUPPORTED_MEDIA:
        raise RuntimeError(f"Unsupported media file: {source}")
    if not source.is_file():
        raise RuntimeError(f"Media file does not exist: {source}")

    input_probe = probe_media(ffprobe, source)
    old_codec = primary_video_codec(input_probe)
    if not old_codec:
        raise RuntimeError("Input has no video stream")

    transcode_video = old_codec not in {"hevc", "h265"}
    metadata_remux = needs_metadata_remux(cfg, job, input_probe)
    input_size = source.stat().st_size
    audio_count = len(streams_of_type(input_probe, "audio"))
    subtitle_count = len(streams_of_type(input_probe, "subtitle"))

    if not transcode_video and not metadata_remux:
        send_transcode_webhook(
            cfg,
            "✅ Codec check completed",
            source.name,
            [
                {"name": "Codec", "value": old_codec.upper(), "inline": True},
                {"name": "Action", "value": "No conversion required", "inline": True},
                {"name": "Size", "value": human_size(input_size), "inline": True},
            ],
            3066993,
        )
        return {
            "action": "skipped",
            "codec": old_codec,
            "file_path": str(source),
            "size": input_size,
        }

    action_text = "HEVC transcode" if transcode_video else "metadata remux"
    send_transcode_webhook(
        cfg,
        "🎬 Transcoding started",
        source.name,
        [
            {"name": "Action", "value": action_text, "inline": True},
            {"name": "Source codec", "value": old_codec.upper(), "inline": True},
            {"name": "Encoder", "value": str(transcode_cfg.get("encoder", "libx265")), "inline": True},
            {"name": "Audio tracks", "value": str(audio_count), "inline": True},
            {"name": "Subtitle tracks", "value": str(subtitle_count), "inline": True},
            {"name": "Input size", "value": human_size(input_size), "inline": True},
        ],
        3447003,
    )

    output_container = str(transcode_cfg.get("output_container", "mkv")).lstrip(".")
    final_path = (
        source
        if source.suffix.lower() == f".{output_container.lower()}"
        else source.with_suffix(f".{output_container}")
    )
    output = final_path.with_name(final_path.stem + ".transcoding." + output_container)
    output.unlink(missing_ok=True)
    started = time.monotonic()

    command = build_ffmpeg_command(
        cfg, job, ffmpeg, source, output, input_probe, transcode_video
    )
    result = run_command(command, timeout=None)

    if result.returncode != 0:
        if not transcode_cfg.get("keep_failed_output", False):
            output.unlink(missing_ok=True)
        error_tail = (result.stderr or "ffmpeg failed")[-3500:]
        raise RuntimeError(error_tail)

    if not output.is_file() or output.stat().st_size == 0:
        raise RuntimeError("FFmpeg returned success but output file is missing or empty")

    output_probe = probe_media(ffprobe, output)
    verify_output(
        input_probe,
        output_probe,
        expect_hevc=transcode_video or old_codec in {"hevc", "h265"},
        max_duration_difference=float(
            transcode_cfg.get("max_duration_difference_seconds", 2.0)
        ),
    )

    output_size = output.stat().st_size
    keep_source = not bool(transcode_cfg.get("delete_source_after_success", True))
    safe_replace(
        source, output, final_path=final_path, keep_source=keep_source
    )
    elapsed = int(time.monotonic() - started)
    new_codec = primary_video_codec(probe_media(ffprobe, final_path))

    send_transcode_webhook(
        cfg,
        "✅ Transcoding completed",
        final_path.name,
        [
            {"name": "Old codec", "value": old_codec.upper(), "inline": True},
            {"name": "New codec", "value": new_codec.upper(), "inline": True},
            {"name": "Duration", "value": f"{elapsed // 3600:02d}:{(elapsed % 3600) // 60:02d}:{elapsed % 60:02d}", "inline": True},
            {"name": "Original size", "value": human_size(input_size), "inline": True},
            {"name": "New size", "value": human_size(output_size), "inline": True},
            {"name": "Audio preserved", "value": f"{len(streams_of_type(output_probe, 'audio'))}/{audio_count}", "inline": True},
            {"name": "Subtitles preserved", "value": f"{len(streams_of_type(output_probe, 'subtitle'))}/{subtitle_count}", "inline": True},
        ],
        3066993,
    )

    return {
        "action": "transcoded" if transcode_video else "remuxed",
        "old_codec": old_codec,
        "new_codec": new_codec,
        "file_path": str(final_path),
        "input_size": input_size,
        "output_size": output_size,
        "elapsed_seconds": elapsed,
    }


def run_one(cfg: Dict[str, Any]) -> bool:
    job = claim_next_job(cfg)
    if not job:
        logging.info("No queued transcode jobs.")
        return False

    job_id = str(job.get("job_id", ""))
    file_name = Path(str(job.get("file_path", ""))).name

    try:
        result = process_job(cfg, job)
        finish_job(cfg, job_id, "done", result=result)
        logging.info("Transcode job completed: %s", file_name)
    except Exception as error:
        logging.exception("Transcode job failed: %s", file_name)
        finish_job(cfg, job_id, "failed", error=str(error))
        send_transcode_webhook(
            cfg,
            "❌ Transcoding failed",
            file_name,
            [
                {"name": "Error", "value": str(error)[-1024:], "inline": False},
                {"name": "Original file", "value": "Preserved", "inline": True},
            ],
            15158332,
        )
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Wardf Nyaa transcode worker")
    parser.add_argument("--daemon", action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=30, help="Daemon polling interval")
    parser.add_argument("--all", action="store_true", help="Process all queued jobs then exit")
    args = parser.parse_args()

    cfg = load_config()
    if not get_transcode_cfg(cfg).get("enabled", False):
        logging.info("Transcoding is disabled in config.json.")
        return 0

    if args.daemon:
        while True:
            run_one(cfg)
            time.sleep(max(5, args.interval))

    if args.all:
        while run_one(cfg):
            cfg = load_config()
        return 0

    run_one(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
