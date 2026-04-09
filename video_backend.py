# -*- coding: utf-8 -*-
from __future__ import annotations

import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

from app_settings import AUDIO_EXTS, FOOTAGE_EXTS, IMAGE_EXTS


def find_ffmpeg() -> Optional[str]:
    return shutil.which("ffmpeg")


def find_ffprobe() -> Optional[str]:
    return shutil.which("ffprobe")


def ffprobe_duration_seconds(path: Path, ffprobe: str, log: Callable[[str], None]) -> Optional[float]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        str(path),
    ]
    log(f"Do do dai audio: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            log(f"Loi ffprobe: {result.stderr or result.stdout}")
            return None
        return float((result.stdout or "").strip())
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        log(f"Loi khi do audio: {exc}")
        return None


def ffprobe_video_size(path: Path, ffprobe: str, log: Callable[[str], None]) -> Optional[Tuple[int, int]]:
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-show_entries",
        "stream=width,height",
        "-of",
        "csv=p=0:s=x",
        str(path),
    ]
    log(f"Doc kich thuoc video: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            log(f"Loi ffprobe video: {result.stderr or result.stdout}")
            return None
        blob = (result.stdout or "").strip()
        if not blob or "x" not in blob:
            return None
        width_text, height_text = blob.split("x", 1)
        return int(width_text), int(height_text)
    except (ValueError, subprocess.TimeoutExpired, OSError) as exc:
        log(f"Loi khi doc kich thuoc video: {exc}")
        return None


def list_images(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in IMAGE_EXTS:
            files.append(path)
    return files


def list_audio_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in AUDIO_EXTS:
            files.append(path)
    return files


def list_mp3_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() == ".mp3":
            files.append(path)
    return files


def list_footage_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for path in sorted(folder.iterdir(), key=lambda item: item.name.lower()):
        if path.is_file() and path.suffix.lower() in FOOTAGE_EXTS:
            files.append(path)
    return files


def dir_has_mp4_video(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    for path in folder.iterdir():
        if path.is_file() and path.suffix.lower() == ".mp4":
            return True
    return False


def list_immediate_subdirs(root: Path) -> List[Path]:
    if not root.is_dir():
        return []
    return sorted([path for path in root.iterdir() if path.is_dir()], key=lambda item: item.name.lower())


def get_image_size(path: Path) -> Optional[Tuple[int, int]]:
    if Image is not None:
        try:
            with Image.open(path) as image_obj:
                return image_obj.size
        except OSError:
            return None
    return None


def analyze_images_same_size(paths: List[Path], log: Callable[[str], None]) -> Tuple[bool, Optional[Tuple[int, int]]]:
    if not paths:
        return True, None
    if Image is None:
        log("Can cai Pillow neu muon kiem tra kich thuoc anh.")
        return True, None
    first = get_image_size(paths[0])
    if first is None:
        log(f"Khong doc duoc anh: {paths[0]}")
        return False, None
    width_0, height_0 = first
    for path in paths[1:]:
        size = get_image_size(path)
        if size is None:
            log(f"Khong doc duoc anh: {path}")
            return False, None
        if size != (width_0, height_0):
            log(f"Anh khac kich thuoc: {path} ({size[0]}x{size[1]}) so voi {paths[0].name} ({width_0}x{height_0})")
            return False, (width_0, height_0)
    return True, (width_0, height_0)


def target_resolution(
    aspect_mode: str,
    quality: str,
    same_size: bool,
    natural_wh: Optional[Tuple[int, int]],
) -> Tuple[int, int, str]:
    quality_key = quality.strip().upper()
    quality_map = {
        "720": {"16:9": (1280, 720), "9:16": (720, 1280)},
        "1080": {"16:9": (1920, 1080), "9:16": (1080, 1920)},
        "2K": {"16:9": (2560, 1440), "9:16": (1440, 2560)},
        "4K": {"16:9": (3840, 2160), "9:16": (2160, 3840)},
    }
    if quality_key not in quality_map:
        quality_key = "1080"

    def dims_16_9() -> Tuple[int, int]:
        return quality_map[quality_key]["16:9"]

    def dims_9_16() -> Tuple[int, int]:
        return quality_map[quality_key]["9:16"]

    if aspect_mode == "16:9":
        width, height = dims_16_9()
        return width, height, f"Co dinh 16:9 ({width}x{height})"
    if aspect_mode == "9:16":
        width, height = dims_9_16()
        return width, height, f"Co dinh 9:16 ({width}x{height})"

    if same_size and natural_wh:
        natural_w, natural_h = natural_wh
        long_target = quality_map[quality_key]["16:9"][0] if natural_w >= natural_h else quality_map[quality_key]["9:16"][1]
        if natural_w >= natural_h:
            width = long_target
            height = max(2, int(round(natural_h * (long_target / natural_w) / 2) * 2))
        else:
            height = long_target
            width = max(2, int(round(natural_w * (long_target / natural_h) / 2) * 2))
        return width, height, f"Theo anh goc, canh dai ~{long_target}px: {width}x{height}"

    if not natural_wh:
        width, height = dims_16_9()
        return width, height, f"Khong xac dinh duoc kich thuoc anh, fallback 16:9 ({width}x{height})"

    width, height = dims_16_9()
    return width, height, f"Anh khong dong kich thuoc, fallback 16:9 ({width}x{height})"


def build_timeline(images: List[Path], audio_duration: float, seconds_per_image: float) -> List[Tuple[Path, float]]:
    if not images or audio_duration <= 0:
        return []
    spi = max(0.1, float(seconds_per_image))
    sequence: List[Tuple[Path, float]] = []
    remaining = audio_duration
    index = 0
    while remaining > 1e-4:
        image = images[index % len(images)]
        duration = min(spi, remaining)
        sequence.append((image, duration))
        remaining -= duration
        index += 1
    return sequence


def choose_random_footage_pool(footages: List[Path], desired_count: int) -> List[Path]:
    if not footages:
        return []
    count = max(1, min(int(desired_count), len(footages)))
    return random.sample(footages, count)


def build_footage_sequence(pool: List[Path], durations: Dict[Path, float], target_duration: float) -> List[Path]:
    usable = [clip for clip in pool if durations.get(clip, 0.0) > 0]
    if not usable:
        return []
    sequence = random.sample(usable, len(usable))
    total = sum(durations[clip] for clip in sequence)
    while total + 1e-4 < target_duration:
        extra = random.sample(usable, len(usable))
        sequence.extend(extra)
        total += sum(durations[clip] for clip in extra)
    return sequence


def escape_ffconcat_path(path: Path) -> str:
    escaped = path.resolve().as_posix()
    escaped = escaped.replace("'", "'\\''")
    return escaped


def write_concat_file(timeline: List[Tuple[Path, float]], concat_path: Path, log: Callable[[str], None]) -> None:
    lines = ["ffconcat version 1.0"]
    for image, duration in timeline:
        lines.append(f"file '{escape_ffconcat_path(image)}'")
        lines.append(f"duration {duration:.6f}")
    if timeline:
        last = timeline[-1][0]
        lines.append(f"file '{escape_ffconcat_path(last)}'")
    text = "\n".join(lines) + "\n"
    concat_path.write_text(text, encoding="utf-8")
    log(f"Da ghi danh sach concat ({len(timeline)} doan): {concat_path}")


def move_source_to_backup(src: Path, backup_dir: Path, log: Callable[[str], None]) -> bool:
    try:
        backup_dir.mkdir(parents=True, exist_ok=True)
        destination = backup_dir / src.name
        if destination.exists():
            stem = src.stem
            suffix = src.suffix
            index = 1
            while True:
                candidate = backup_dir / f"{stem}_{index}{suffix}"
                if not candidate.exists():
                    destination = candidate
                    break
                index += 1
        shutil.move(str(src), str(destination))
        log(f"Da chuyen file nguon vao backup: {destination}")
        return True
    except (OSError, shutil.Error) as exc:
        log(f"Loi khi chuyen vao backup: {exc}")
        return False


def vf_scale_pad(out_w: int, out_h: int) -> str:
    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def ensure_even_dimensions(width: int, height: int) -> Tuple[int, int]:
    width = max(2, int(width) - (int(width) % 2))
    height = max(2, int(height) - (int(height) % 2))
    return width, height


def clamp_voice_speed(speed: float) -> float:
    return max(0.25, min(4.0, speed))


def build_atempo_chain(speed: float) -> Optional[str]:
    value = clamp_voice_speed(speed)
    if abs(value - 1.0) < 1e-6:
        return None
    parts: List[str] = []
    current = float(value)
    while current > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        current /= 2.0
    while current < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        current /= 0.5
    parts.append(f"atempo={current:.6f}".rstrip("0").rstrip("."))
    return ",".join(parts)


def _subprocess_no_window_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


_ENCODER_LINE_RE = re.compile(r"^\s*V\S*\s+(\S+)\s+(.+)$")


def ffmpeg_available_video_encoders(ffmpeg: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        result = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **_subprocess_no_window_kwargs(),
        )
        blob = (result.stderr or "") + "\n" + (result.stdout or "")
        for line in blob.splitlines():
            match = _ENCODER_LINE_RE.match(line)
            if match:
                out[match.group(1)] = match.group(2).strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def video_encode_arguments(encoder_id: str) -> List[str]:
    encoder = (encoder_id or "libx264").strip()
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if encoder == "h264_nvenc":
        return ["-c:v", "h264_nvenc", "-preset", "p4", "-rc", "vbr", "-cq", "23", "-b:v", "0", "-pix_fmt", "yuv420p"]
    if encoder == "h264_qsv":
        return ["-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "23", "-pix_fmt", "yuv420p"]
    if encoder == "h264_amf":
        return ["-c:v", "h264_amf", "-quality", "balanced", "-rc", "vbr_latency", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    if encoder == "h264_mf":
        return ["-c:v", "h264_mf", "-rate_control", "quality", "-quality", "75", "-pix_fmt", "yuv420p"]
    if encoder == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    return ["-c:v", encoder, "-pix_fmt", "yuv420p"]


def ffmpeg_encoder_smoke_test(ffmpeg: str, enc: str, out_w: int, out_h: int) -> bool:
    if enc == "libx264":
        return True
    out_w, out_h = ensure_even_dimensions(out_w, out_h)
    vf = vf_scale_pad(out_w, out_h)
    encode_args = video_encode_arguments(enc)
    temp_out = Path(tempfile.gettempdir()) / f"vsmoke_{os.getpid()}_{time.time_ns()}.mp4"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={out_w}x{out_h}:d=0.25",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=channel_layout=stereo:sample_rate=44100",
        "-t",
        "0.25",
        "-vf",
        vf,
        "-map",
        "0:v",
        "-map",
        "1:a",
        "-shortest",
        *encode_args,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(temp_out),
    ]
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **_subprocess_no_window_kwargs(),
        )
        return result.returncode == 0 and temp_out.is_file() and temp_out.stat().st_size > 200
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            if temp_out.is_file():
                temp_out.unlink()
        except OSError:
            pass


GPU_TRY_ORDER = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_mf", "h264_videotoolbox"]


def resolve_encoder_choice(
    ffmpeg: str,
    mode: str,
    enc_map: Dict[str, str],
    width: int,
    height: int,
    log: Callable[[str], None],
) -> str:
    if mode == "libx264":
        return "libx264"
    log("Che do GPU: thu NVENC -> QSV -> AMF -> encoder GPU khac co san.")
    for encoder in GPU_TRY_ORDER:
        if encoder not in enc_map:
            continue
        log(f"  -> Thu {encoder}...")
        if ffmpeg_encoder_smoke_test(ffmpeg, encoder, width, height):
            log(f"  -> Dung {encoder}.")
            return encoder
        log(f"  -> {encoder} khong kha dung, thu tiep.")
    log("  -> Khong co GPU encoder phu hop, fallback ve libx264.")
    return "libx264"


def ffmpeg_preprocess_atempo(
    ffmpeg: str,
    voice: Path,
    af_chain: str,
    dest: Path,
    log: Callable[[str], None],
) -> bool:
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-i",
        str(voice),
        "-af",
        af_chain,
        "-vn",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        str(dest),
    ]
    log(f"Tien xu ly audio atempo: {' '.join(cmd)}")
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
        **_subprocess_no_window_kwargs(),
    )
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "").strip()
        if err:
            log(err[-2500:])
        return False
    return True


def run_ffmpeg(
    ffmpeg: str,
    concat_list: Path,
    audio: Path,
    output_video: Path,
    out_w: int,
    out_h: int,
    log: Callable[[str], None],
    on_line: Optional[Callable[[str], None]] = None,
    audio_atempo_chain: Optional[str] = None,
    video_encoder: str = "libx264",
) -> bool:
    vf = vf_scale_pad(out_w, out_h)
    video_args = video_encode_arguments(video_encoder)
    temp_audio: Optional[Path] = None
    audio_in = audio
    try:
        if audio_atempo_chain:
            fd, name = tempfile.mkstemp(suffix=".m4a", prefix="vslideshow_at_")
            os.close(fd)
            temp_audio = Path(name)
            if not ffmpeg_preprocess_atempo(ffmpeg, audio, audio_atempo_chain, temp_audio, log):
                log("Loi tien xu ly audio (atempo).")
                return False
            audio_in = temp_audio

        cmd = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-i",
            str(audio_in),
            "-vf",
            vf,
            "-r",
            "30",
            "-map",
            "0:v",
            "-map",
            "1:a",
            *video_args,
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-shortest",
            "-movflags",
            "+faststart",
            str(output_video),
        ]
        log(f"FFmpeg: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_subprocess_no_window_kwargs(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
                if on_line:
                    on_line(line)
        proc.wait()
        if proc.returncode != 0:
            log(f"FFmpeg thoat ma {proc.returncode}")
            return False
        return True
    except OSError as exc:
        log(f"Loi chay FFmpeg: {exc}")
        return False
    finally:
        if temp_audio is not None and temp_audio.is_file():
            try:
                temp_audio.unlink()
            except OSError:
                pass


def run_ffmpeg_footage(
    ffmpeg: str,
    footage_sequence: List[Path],
    audio: Path,
    output_video: Path,
    out_w: int,
    out_h: int,
    log: Callable[[str], None],
    on_line: Optional[Callable[[str], None]] = None,
    audio_atempo_chain: Optional[str] = None,
    video_encoder: str = "libx264",
) -> bool:
    if not footage_sequence:
        log("Loi: khong co footage de render.")
        return False
    video_args = video_encode_arguments(video_encoder)
    temp_audio: Optional[Path] = None
    audio_in = audio
    try:
        if audio_atempo_chain:
            fd, name = tempfile.mkstemp(suffix=".m4a", prefix="vfootage_at_")
            os.close(fd)
            temp_audio = Path(name)
            if not ffmpeg_preprocess_atempo(ffmpeg, audio, audio_atempo_chain, temp_audio, log):
                log("Loi tien xu ly audio (atempo).")
                return False
            audio_in = temp_audio

        vf = vf_scale_pad(out_w, out_h)
        cmd = [ffmpeg, "-y", "-hide_banner"]
        for clip in footage_sequence:
            cmd.extend(["-i", str(clip)])
        cmd.extend(["-i", str(audio_in)])

        parts: List[str] = []
        concat_inputs: List[str] = []
        for index in range(len(footage_sequence)):
            parts.append(f"[{index}:v]{vf},fps=30,format=yuv420p,setpts=PTS-STARTPTS[v{index}]")
            concat_inputs.append(f"[v{index}]")
        parts.append("".join(concat_inputs) + f"concat=n={len(footage_sequence)}:v=1:a=0[vout]")

        cmd.extend(
            [
                "-filter_complex",
                ";".join(parts),
                "-map",
                "[vout]",
                "-map",
                f"{len(footage_sequence)}:a",
                "-r",
                "30",
                *video_args,
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                "-movflags",
                "+faststart",
                str(output_video),
            ]
        )
        log(f"FFmpeg footage: {' '.join(cmd)}")
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            **_subprocess_no_window_kwargs(),
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(line)
                if on_line:
                    on_line(line)
        proc.wait()
        if proc.returncode != 0:
            log(f"FFmpeg footage thoat ma {proc.returncode}")
            return False
        return True
    except OSError as exc:
        log(f"Loi chay FFmpeg footage: {exc}")
        return False
    finally:
        if temp_audio is not None and temp_audio.is_file():
            try:
                temp_audio.unlink()
            except OSError:
                pass


_time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:[.,]\d+)?)")


def parse_ffmpeg_time_sec(line: str) -> Optional[float]:
    match = _time_re.search(line)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    sec_float = float(seconds.replace(",", ".")) if seconds else 0.0
    return int(hours) * 3600 + int(minutes) * 60 + sec_float
