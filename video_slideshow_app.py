# -*- coding: utf-8 -*-
"""
Công cụ ghép ảnh + voice MP3 thành video (FFmpeg).
Yêu cầu: FFmpeg có trong PATH (https://ffmpeg.org/download.html).
"""

from __future__ import annotations

import json
import os
import sys
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

try:
    from PIL import Image
except ImportError:
    Image = None  # type: ignore

import tkinter as tk
from tkinter import filedialog, messagebox, ttk


SETTINGS_FILE = Path(__file__).resolve().parent / "video_editor_settings.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}

# (id lưu JSON, nhãn hiển thị)
ENCODER_OPTIONS: List[Tuple[str, str]] = [
    ("auto_gpu", "GPU — NVENC / QSV / AMF (nhẹ CPU, thường ít ồn quạt)"),
    ("libx264", "CPU — libx264 (chỉ CPU)"),
]


@dataclass
class AppSettings:
    voice_path: str = ""
    image_dir: str = ""
    output_dir: str = ""
    seconds_per_image: float = 4.0
    voice_speed: float = 1.0  # 1 = gốc; >1 nhanh hơn (video ngắn hơn)
    video_encoder: str = "auto_gpu"  # libx264 | auto_gpu
    quality: str = "1080"  # "720" | "1080"
    aspect: str = "auto"  # "auto" | "16:9" | "9:16"
    # Nâng cao: thư mục nhiều voice → render lần lượt, mỗi file một MP4
    use_voice_folder: bool = False
    voice_folder: str = ""
    # Thư mục gốc: mỗi thư mục con (một tầng) = một dự án (1 mp3 + ảnh), MP4 trong thư mục con
    use_root_folder: bool = False
    root_folder: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AppSettings":
        return cls(
            voice_path=str(d.get("voice_path", "")),
            image_dir=str(d.get("image_dir", "")),
            output_dir=str(d.get("output_dir", "")),
            seconds_per_image=float(d.get("seconds_per_image", 4.0)),
            voice_speed=float(d.get("voice_speed", 1.0)),
            video_encoder=str(d.get("video_encoder", "auto_gpu")),
            quality=str(d.get("quality", "1080")),
            aspect=str(d.get("aspect", "auto")),
            use_voice_folder=bool(d.get("use_voice_folder", False)),
            voice_folder=str(d.get("voice_folder", "")),
            use_root_folder=bool(d.get("use_root_folder", False)),
            root_folder=str(d.get("root_folder", "")),
        )


def load_settings() -> AppSettings:
    if SETTINGS_FILE.is_file():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return AppSettings.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return AppSettings()


def save_settings(s: AppSettings) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(s.to_dict(), f, ensure_ascii=False, indent=2)
    except OSError:
        pass


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
    log(f"Đo độ dài audio: {' '.join(cmd)}")
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
        )
        if r.returncode != 0:
            log(f"Lỗi ffprobe: {r.stderr or r.stdout}")
            return None
        return float((r.stdout or "").strip())
    except (ValueError, subprocess.TimeoutExpired, OSError) as e:
        log(f"Lỗi khi đo audio: {e}")
        return None


def list_images(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            files.append(p)
    return files


def list_audio_files(folder: Path) -> List[Path]:
    if not folder.is_dir():
        return []
    files = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() in AUDIO_EXTS:
            files.append(p)
    return files


def list_mp3_files(folder: Path) -> List[Path]:
    """Chỉ *.mp3 — dùng cho chế độ thư mục gốc."""
    if not folder.is_dir():
        return []
    files = []
    for p in sorted(folder.iterdir(), key=lambda x: x.name.lower()):
        if p.is_file() and p.suffix.lower() == ".mp3":
            files.append(p)
    return files


def dir_has_mp4_video(folder: Path) -> bool:
    """Có ít nhất một file .mp4 trực tiếp trong thư mục (một tầng)."""
    if not folder.is_dir():
        return False
    for p in folder.iterdir():
        if p.is_file() and p.suffix.lower() == ".mp4":
            return True
    return False


def list_immediate_subdirs(root: Path) -> List[Path]:
    """Thư mục con trực tiếp (một tầng), sắp xếp tên."""
    if not root.is_dir():
        return []
    return sorted([p for p in root.iterdir() if p.is_dir()], key=lambda x: x.name.lower())


def get_image_size(path: Path) -> Optional[Tuple[int, int]]:
    if Image is not None:
        try:
            with Image.open(path) as im:
                return im.size
        except OSError:
            return None
    return None


def analyze_images_same_size(paths: List[Path], log: Callable[[str], None]) -> Tuple[bool, Optional[Tuple[int, int]]]:
    if not paths:
        return True, None
    if Image is None:
        log("Cảnh báo: chưa cài Pillow — không kiểm tra được kích thước ảnh. Chạy: pip install -r requirements.txt")
        return True, None
    first = get_image_size(paths[0])
    if first is None:
        log(f"Không đọc được ảnh: {paths[0]}")
        return False, None
    w0, h0 = first
    for p in paths[1:]:
        sz = get_image_size(p)
        if sz is None:
            log(f"Không đọc được ảnh: {p}")
            return False, None
        if sz != (w0, h0):
            log(f"Ảnh khác kích thước: {p} ({sz[0]}x{sz[1]}) so với {paths[0].name} ({w0}x{h0})")
            return False, (w0, h0)
    return True, (w0, h0)


def target_resolution(
    aspect_mode: str,
    quality: str,
    same_size: bool,
    natural_wh: Optional[Tuple[int, int]],
) -> Tuple[int, int, str]:
    q = quality.strip()
    if q not in ("720", "1080"):
        q = "1080"

    def dims_16_9() -> Tuple[int, int]:
        return (1280, 720) if q == "720" else (1920, 1080)

    def dims_9_16() -> Tuple[int, int]:
        return (720, 1280) if q == "720" else (1080, 1920)

    if aspect_mode == "16:9":
        w, h = dims_16_9()
        return w, h, f"Cố định 16:9 ({w}x{h})"
    if aspect_mode == "9:16":
        w, h = dims_9_16()
        return w, h, f"Cố định 9:16 ({w}x{h})"

    if same_size and natural_wh:
        nw, nh = natural_wh
        long_target = 1080 if q == "1080" else 720
        if nw >= nh:
            w = long_target
            h = max(2, int(round(nh * (long_target / nw) / 2) * 2))
        else:
            h = long_target
            w = max(2, int(round(nw * (long_target / nh) / 2) * 2))
        return w, h, f"Theo ảnh (giữ tỷ lệ, cạnh dài ~{long_target}px): {w}x{h}"

    if not natural_wh:
        w, h = dims_16_9()
        return w, h, f"Không xác định được kích thước ảnh (cài Pillow) → 16:9 ({w}x{h})"

    w, h = dims_16_9()
    return w, h, f"Ảnh không đồng kích thước → mặc định 16:9 ({w}x{h})"


def build_timeline(
    images: List[Path],
    audio_duration: float,
    seconds_per_image: float,
) -> List[Tuple[Path, float]]:
    if not images or audio_duration <= 0:
        return []
    spi = max(0.1, float(seconds_per_image))
    seq: List[Tuple[Path, float]] = []
    remaining = audio_duration
    i = 0
    while remaining > 1e-4:
        img = images[i % len(images)]
        dur = min(spi, remaining)
        seq.append((img, dur))
        remaining -= dur
        i += 1
    return seq


def escape_ffconcat_path(p: Path) -> str:
    s = p.resolve().as_posix()
    s = s.replace("'", "'\\''")
    return s


def write_concat_file(timeline: List[Tuple[Path, float]], concat_path: Path, log: Callable[[str], None]) -> None:
    lines = ["ffconcat version 1.0"]
    for img, dur in timeline:
        lines.append(f"file '{escape_ffconcat_path(img)}'")
        lines.append(f"duration {dur:.6f}")
    if timeline:
        last = timeline[-1][0]
        lines.append(f"file '{escape_ffconcat_path(last)}'")
    text = "\n".join(lines) + "\n"
    concat_path.write_text(text, encoding="utf-8")
    log(f"Đã ghi danh sách concat ({len(timeline)} đoạn): {concat_path}")


def vf_scale_pad(out_w: int, out_h: int) -> str:
    return (
        f"scale={out_w}:{out_h}:force_original_aspect_ratio=decrease,"
        f"pad={out_w}:{out_h}:(ow-iw)/2:(oh-ih)/2,setsar=1"
    )


def ensure_even_dimensions(w: int, h: int) -> Tuple[int, int]:
    w = max(2, int(w) - (int(w) % 2))
    h = max(2, int(h) - (int(h) % 2))
    return w, h


def clamp_voice_speed(speed: float) -> float:
    return max(0.25, min(4.0, speed))


def build_atempo_chain(speed: float) -> Optional[str]:
    s = clamp_voice_speed(speed)
    if abs(s - 1.0) < 1e-6:
        return None
    parts: List[str] = []
    f = float(s)
    while f > 2.0 + 1e-9:
        parts.append("atempo=2.0")
        f /= 2.0
    while f < 0.5 - 1e-9:
        parts.append("atempo=0.5")
        f /= 0.5
    parts.append(f"atempo={f:.6f}".rstrip("0").rstrip("."))
    return ",".join(parts)


def _subprocess_no_window_kwargs() -> dict:
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


_ENCODER_LINE_RE = re.compile(r"^\s*V\S*\s+(\S+)\s+(.+)$")


def ffmpeg_available_video_encoders(ffmpeg: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        r = subprocess.run(
            [ffmpeg, "-hide_banner", "-encoders"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **_subprocess_no_window_kwargs(),
        )
        blob = (r.stderr or "") + "\n" + (r.stdout or "")
        for ln in blob.splitlines():
            m = _ENCODER_LINE_RE.match(ln)
            if m:
                out[m.group(1)] = m.group(2).strip()
    except (OSError, subprocess.TimeoutExpired):
        pass
    return out


def video_encode_arguments(encoder_id: str) -> List[str]:
    e = (encoder_id or "libx264").strip()
    if e == "libx264":
        return ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p"]
    if e == "h264_nvenc":
        return [
            "-c:v",
            "h264_nvenc",
            "-preset",
            "p4",
            "-rc",
            "vbr",
            "-cq",
            "23",
            "-b:v",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    if e == "h264_qsv":
        return [
            "-c:v",
            "h264_qsv",
            "-preset",
            "medium",
            "-global_quality",
            "23",
            "-pix_fmt",
            "yuv420p",
        ]
    if e == "h264_amf":
        return [
            "-c:v",
            "h264_amf",
            "-quality",
            "balanced",
            "-rc",
            "vbr_latency",
            "-b:v",
            "8M",
            "-pix_fmt",
            "yuv420p",
        ]
    if e == "h264_mf":
        return ["-c:v", "h264_mf", "-rate_control", "quality", "-quality", "75", "-pix_fmt", "yuv420p"]
    if e == "h264_videotoolbox":
        return ["-c:v", "h264_videotoolbox", "-b:v", "8M", "-pix_fmt", "yuv420p"]
    return ["-c:v", e, "-pix_fmt", "yuv420p"]


def ffmpeg_encoder_smoke_test(ffmpeg: str, enc: str, ow: int, oh: int) -> bool:
    if enc == "libx264":
        return True
    ow, oh = ensure_even_dimensions(ow, oh)
    vf = vf_scale_pad(ow, oh)
    v = video_encode_arguments(enc)
    out_f = Path(tempfile.gettempdir()) / f"vsmoke_{os.getpid()}_{time.time_ns()}.mp4"
    cmd = [
        ffmpeg,
        "-y",
        "-hide_banner",
        "-f",
        "lavfi",
        "-i",
        f"color=c=black:s={ow}x{oh}:d=0.25",
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
        *v,
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        str(out_f),
    ]
    try:
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
            **_subprocess_no_window_kwargs(),
        )
        ok = r.returncode == 0 and out_f.is_file() and out_f.stat().st_size > 200
        return ok
    except (OSError, subprocess.TimeoutExpired):
        return False
    finally:
        try:
            if out_f.is_file():
                out_f.unlink()
        except OSError:
            pass


GPU_TRY_ORDER = ["h264_nvenc", "h264_qsv", "h264_amf", "h264_mf", "h264_videotoolbox"]


def resolve_encoder_choice(
    ffmpeg: str,
    mode: str,
    enc_map: Dict[str, str],
    w: int,
    h: int,
    log: Callable[[str], None],
) -> str:
    """mode: libx264 | auto_gpu"""
    if mode == "libx264":
        return "libx264"
    log("Chế độ GPU: thử NVENC → QSV → AMF → … (encoder có trong FFmpeg).")
    for enc in GPU_TRY_ORDER:
        if enc not in enc_map:
            continue
        log(f"  → Thử {enc}…")
        if ffmpeg_encoder_smoke_test(ffmpeg, enc, w, h):
            log(f"  → Dùng {enc} (encode video trên GPU, CPU nhẹ hơn — thường ít ồn quạt hơn).")
            return enc
        log(f"  → {enc} không chạy được, thử tiếp…")
    log("  → Không có GPU encoder ổn định — dùng libx264 (CPU).")
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
    log(f"Tiền xử lý audio (tốc độ voice): {' '.join(cmd)}")
    r = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=7200,
        **_subprocess_no_window_kwargs(),
    )
    if r.returncode != 0:
        err = (r.stderr or r.stdout or "").strip()
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
    """
    Audio có atempo: xuất file tạm trước, rồi ghép với -vf (ổn định với NVENC/GPU).
    """
    vf = vf_scale_pad(out_w, out_h)
    v_enc = video_encode_arguments(video_encoder)
    temp_audio: Optional[Path] = None
    audio_in = audio
    try:
        if audio_atempo_chain:
            fd, name = tempfile.mkstemp(suffix=".m4a", prefix="vslideshow_at_")
            os.close(fd)
            temp_audio = Path(name)
            if not ffmpeg_preprocess_atempo(ffmpeg, audio, audio_atempo_chain, temp_audio, log):
                log("Lỗi tiền xử lý audio (atempo).")
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
            *v_enc,
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
            log(f"FFmpeg thoát mã {proc.returncode}")
            return False
        return True
    except OSError as e:
        log(f"Lỗi chạy FFmpeg: {e}")
        return False
    finally:
        if temp_audio is not None and temp_audio.is_file():
            try:
                temp_audio.unlink()
            except OSError:
                pass


# FFmpeg có thể in time=00:00:05 hoặc time=00:00:05.12 (một số bản dùng dấu phẩy)
_time_re = re.compile(r"time=(\d+):(\d+):(\d+(?:[.,]\d+)?)")


def parse_ffmpeg_time_sec(line: str) -> Optional[float]:
    m = _time_re.search(line)
    if not m:
        return None
    h, mnt, sec = m.groups()
    sec_f = float(sec.replace(",", ".")) if sec else 0.0
    return int(h) * 3600 + int(mnt) * 60 + sec_f


class VideoEditorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Ghép ảnh + Voice → Video")
        self.geometry("840x820")
        self.minsize(740, 660)

        self._log_queue: queue.Queue[str] = queue.Queue()
        self._settings = load_settings()
        self._render_thread: Optional[threading.Thread] = None

        self._build_ui()
        self._apply_settings_to_ui()
        self.after(100, self._drain_log_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)

        root_lf = ttk.LabelFrame(frm, text="Thư mục gốc (batch theo dự án)", padding=6)
        root_lf.pack(fill=tk.X, **pad)
        self.var_use_root_folder = tk.BooleanVar(value=False)
        self._chk_root = ttk.Checkbutton(
            root_lf,
            text="Bật: mỗi thư mục con (một cấp dưới gốc) = 1 video — trong đó đúng 1 file .mp3 và ít nhất một ảnh; "
            "MP4 lưu trong thư mục con. Đã có file .mp4 trong thư mục con thì bỏ qua (ghi log). "
            "Khi bật, tắt hiệu lực file voice / thư mục ảnh / batch voice / thư mục ra.",
            variable=self.var_use_root_folder,
            command=self._on_root_mode_changed,
        )
        self._chk_root.pack(anchor=tk.W)
        row_root = ttk.Frame(root_lf)
        row_root.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_root, text="Thư mục gốc:").pack(side=tk.LEFT)
        self.var_root_folder = tk.StringVar()
        self._entry_root_folder = ttk.Entry(row_root, textvariable=self.var_root_folder)
        self._entry_root_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_root_folder = ttk.Button(row_root, text="Chọn…", command=self._pick_root_folder)
        self._btn_root_folder.pack(side=tk.LEFT)

        row0 = ttk.Frame(frm)
        row0.pack(fill=tk.X, **pad)
        ttk.Label(row0, text="File voice (MP3):").pack(side=tk.LEFT)
        self.var_voice = tk.StringVar()
        self._entry_voice_file = ttk.Entry(row0, textvariable=self.var_voice)
        self._entry_voice_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_voice_file = ttk.Button(row0, text="Chọn…", command=self._pick_voice)
        self._btn_voice_file.pack(side=tk.LEFT)

        adv = ttk.LabelFrame(frm, text="Nâng cao — nhiều voice trong thư mục", padding=6)
        adv.pack(fill=tk.X, **pad)
        self.var_use_voice_folder = tk.BooleanVar(value=False)
        self._chk_voice_folder = ttk.Checkbutton(
            adv,
            text="Chạy lần lượt từng file voice trong thư mục (ghép với ảnh, lặp ảnh; mỗi voice → một MP4)",
            variable=self.var_use_voice_folder,
            command=self._sync_path_input_widgets,
        )
        self._chk_voice_folder.pack(anchor=tk.W)
        row_adv = ttk.Frame(adv)
        row_adv.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(row_adv, text="Thư mục voice:").pack(side=tk.LEFT)
        self.var_voice_folder = tk.StringVar()
        self._entry_voice_folder = ttk.Entry(row_adv, textvariable=self.var_voice_folder)
        self._entry_voice_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_voice_folder = ttk.Button(row_adv, text="Chọn…", command=self._pick_voice_folder)
        self._btn_voice_folder.pack(side=tk.LEFT)

        row1 = ttk.Frame(frm)
        row1.pack(fill=tk.X, **pad)
        ttk.Label(row1, text="Thư mục ảnh:").pack(side=tk.LEFT)
        self.var_imgdir = tk.StringVar()
        self._entry_imgdir = ttk.Entry(row1, textvariable=self.var_imgdir)
        self._entry_imgdir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_imgdir = ttk.Button(row1, text="Chọn…", command=self._pick_imgdir)
        self._btn_imgdir.pack(side=tk.LEFT)

        row2 = ttk.Frame(frm)
        row2.pack(fill=tk.X, **pad)
        ttk.Label(row2, text="Thư mục video ra:").pack(side=tk.LEFT)
        self.var_outdir = tk.StringVar()
        self._entry_outdir = ttk.Entry(row2, textvariable=self.var_outdir)
        self._entry_outdir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_outdir = ttk.Button(row2, text="Chọn…", command=self._pick_outdir)
        self._btn_outdir.pack(side=tk.LEFT)

        opt = ttk.LabelFrame(frm, text="Cài đặt", padding=8)
        opt.pack(fill=tk.X, **pad)

        r3 = ttk.Frame(opt)
        r3.pack(fill=tk.X, pady=2)
        ttk.Label(r3, text="Mỗi ảnh (giây):").pack(side=tk.LEFT)
        self.var_spi = tk.StringVar(value="4")
        ttk.Entry(r3, textvariable=self.var_spi, width=8).pack(side=tk.LEFT, padx=6)

        ttk.Label(r3, text="Chất lượng:").pack(side=tk.LEFT, padx=(16, 0))
        self.var_quality = tk.StringVar(value="1080")
        ttk.Combobox(
            r3,
            textvariable=self.var_quality,
            values=("720", "1080"),
            state="readonly",
            width=6,
        ).pack(side=tk.LEFT, padx=6)

        ttk.Label(r3, text="Tỷ lệ:").pack(side=tk.LEFT, padx=(16, 0))
        self.var_aspect = tk.StringVar(value="auto")
        ttk.Combobox(
            r3,
            textvariable=self.var_aspect,
            values=("auto", "16:9", "9:16"),
            state="readonly",
            width=10,
        ).pack(side=tk.LEFT, padx=6)

        r_vs = ttk.Frame(opt)
        r_vs.pack(fill=tk.X, pady=4)
        ttk.Label(r_vs, text="Tốc độ voice (×):").pack(side=tk.LEFT)
        self.var_voice_speed = tk.StringVar(value="1")
        ttk.Entry(r_vs, textvariable=self.var_voice_speed, width=8).pack(side=tk.LEFT, padx=6)
        ttk.Label(
            r_vs,
            text="1 = gốc; >1 nhanh hơn (video ngắn hơn); <1 chậm hơn. Khuyến nghị 0,25–4.",
            foreground="#555",
        ).pack(side=tk.LEFT, padx=(8, 0))

        r_enc = ttk.Frame(opt)
        r_enc.pack(fill=tk.X, pady=4)
        ttk.Label(r_enc, text="Encode video:").pack(side=tk.LEFT)
        self.var_enc_display = tk.StringVar()
        self.combo_enc = ttk.Combobox(
            r_enc,
            textvariable=self.var_enc_display,
            values=[lbl for _eid, lbl in ENCODER_OPTIONS],
            state="readonly",
            width=52,
        )
        self.combo_enc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        btn_row = ttk.Frame(frm)
        btn_row.pack(fill=tk.X, **pad)
        self.btn_render = ttk.Button(btn_row, text="Bắt đầu render", command=self._start_render)
        self.btn_render.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Lưu cài đặt", command=self._save_settings_clicked).pack(side=tk.LEFT, padx=8)

        prog_row = ttk.Frame(frm)
        prog_row.pack(fill=tk.X, **pad)
        self.var_progress = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(prog_row, variable=self.var_progress, maximum=100)
        self.progress.pack(fill=tk.X, side=tk.LEFT, expand=True)
        self.var_eta = tk.StringVar(value="Ước lượng: —")
        ttk.Label(prog_row, textvariable=self.var_eta, width=28).pack(side=tk.RIGHT, padx=6)

        lf = ttk.LabelFrame(frm, text="Nhật ký", padding=6)
        lf.pack(fill=tk.BOTH, expand=True, **pad)
        self.txt = tk.Text(lf, height=18, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        sb = ttk.Scrollbar(lf, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

        ttk.Label(
            frm,
            text="FFmpeg trong PATH. GPU: lỗi sẽ hạ CPU. Thư mục gốc: mỗi thư mục con = 1 MP4 cạnh .mp3/ảnh; "
            "batch voice: nhiều MP4 vào một thư mục ra.",
            foreground="#444",
        ).pack(anchor=tk.W)

    def _apply_settings_to_ui(self) -> None:
        s = self._settings
        self.var_voice.set(s.voice_path)
        self.var_use_voice_folder.set(bool(s.use_voice_folder))
        self.var_voice_folder.set(s.voice_folder)
        self.var_use_root_folder.set(bool(s.use_root_folder))
        self.var_root_folder.set(s.root_folder)
        if s.use_root_folder:
            self.var_use_voice_folder.set(False)
        self.var_imgdir.set(s.image_dir)
        self.var_outdir.set(s.output_dir)
        self.var_spi.set(str(s.seconds_per_image))
        self.var_quality.set(s.quality if s.quality in ("720", "1080") else "1080")
        self.var_aspect.set(s.aspect if s.aspect in ("auto", "16:9", "9:16") else "auto")
        self.var_voice_speed.set(str(s.voice_speed))
        ve = s.video_encoder if s.video_encoder in ("libx264", "auto_gpu") else "auto_gpu"
        for eid, lbl in ENCODER_OPTIONS:
            if eid == ve:
                self.var_enc_display.set(lbl)
                break
        else:
            self.var_enc_display.set(ENCODER_OPTIONS[0][1])
        self._sync_path_input_widgets()

    def _on_root_mode_changed(self) -> None:
        if self.var_use_root_folder.get():
            self.var_use_voice_folder.set(False)
        self._sync_path_input_widgets()

    def _sync_path_input_widgets(self) -> None:
        root_on = self.var_use_root_folder.get()
        folder_mode = self.var_use_voice_folder.get() and not root_on

        if root_on:
            self._chk_voice_folder.configure(state=tk.DISABLED)
            self._entry_root_folder.configure(state=tk.NORMAL)
            self._btn_root_folder.configure(state=tk.NORMAL)
            for w in (
                self._entry_voice_file,
                self._btn_voice_file,
                self._entry_voice_folder,
                self._btn_voice_folder,
                self._entry_imgdir,
                self._btn_imgdir,
                self._entry_outdir,
                self._btn_outdir,
            ):
                w.configure(state=tk.DISABLED)
            return

        self._chk_voice_folder.configure(state=tk.NORMAL)
        self._entry_root_folder.configure(state=tk.DISABLED)
        self._btn_root_folder.configure(state=tk.DISABLED)

        for w in (self._entry_imgdir, self._btn_imgdir, self._entry_outdir, self._btn_outdir):
            w.configure(state=tk.NORMAL)

        st_file = tk.DISABLED if folder_mode else tk.NORMAL
        st_fold = tk.NORMAL if folder_mode else tk.DISABLED
        self._entry_voice_file.configure(state=st_file)
        self._btn_voice_file.configure(state=st_file)
        self._entry_voice_folder.configure(state=st_fold)
        self._btn_voice_folder.configure(state=st_fold)

    def _encoder_id_from_display(self, display: str) -> str:
        for eid, lbl in ENCODER_OPTIONS:
            if lbl == display:
                return eid
        return "auto_gpu"

    def _gather_settings_from_ui(self) -> AppSettings:
        try:
            spi = float(self.var_spi.get().replace(",", "."))
        except ValueError:
            spi = 4.0
        try:
            vs = float(self.var_voice_speed.get().replace(",", "."))
        except ValueError:
            vs = 1.0
        return AppSettings(
            voice_path=self.var_voice.get().strip(),
            image_dir=self.var_imgdir.get().strip(),
            output_dir=self.var_outdir.get().strip(),
            seconds_per_image=spi,
            voice_speed=vs,
            video_encoder=self._encoder_id_from_display(self.var_enc_display.get()),
            quality=self.var_quality.get(),
            aspect=self.var_aspect.get(),
            use_voice_folder=self.var_use_voice_folder.get(),
            voice_folder=self.var_voice_folder.get().strip(),
            use_root_folder=self.var_use_root_folder.get(),
            root_folder=self.var_root_folder.get().strip(),
        )

    def _save_settings_clicked(self) -> None:
        self._settings = self._gather_settings_from_ui()
        save_settings(self._settings)
        self._log("Đã lưu cài đặt vào video_editor_settings.json")

    def _pick_voice(self) -> None:
        if self.var_use_root_folder.get() or self.var_use_voice_folder.get():
            return
        p = filedialog.askopenfilename(filetypes=[("MP3", "*.mp3"), ("Audio", "*.mp3 *.m4a *.wav"), ("All", "*.*")])
        if p:
            self.var_voice.set(p)

    def _pick_voice_folder(self) -> None:
        if self.var_use_root_folder.get() or not self.var_use_voice_folder.get():
            return
        p = filedialog.askdirectory()
        if p:
            self.var_voice_folder.set(p)

    def _pick_root_folder(self) -> None:
        if not self.var_use_root_folder.get():
            return
        p = filedialog.askdirectory()
        if p:
            self.var_root_folder.set(p)

    def _pick_imgdir(self) -> None:
        if self.var_use_root_folder.get():
            return
        p = filedialog.askdirectory()
        if p:
            self.var_imgdir.set(p)

    def _pick_outdir(self) -> None:
        if self.var_use_root_folder.get():
            return
        p = filedialog.askdirectory()
        if p:
            self.var_outdir.set(p)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_queue.put(f"[{ts}] {msg}")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self.txt.configure(state=tk.NORMAL)
                self.txt.insert(tk.END, line + "\n")
                self.txt.see(tk.END)
                self.txt.configure(state=tk.DISABLED)
        except queue.Empty:
            pass
        self.after(100, self._drain_log_queue)

    def _start_render(self) -> None:
        if self._render_thread and self._render_thread.is_alive():
            messagebox.showinfo("Đang chạy", "Đang render, vui lòng đợi.")
            return

        s = self._gather_settings_from_ui()

        if s.use_root_folder:
            root = Path(s.root_folder)
            if not root.is_dir():
                messagebox.showerror("Thư mục gốc", "Chọn thư mục gốc hợp lệ.")
                return
            try:
                spi = float(self.var_spi.get().replace(",", "."))
                if spi <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Số không hợp lệ", "Mỗi ảnh (giây) phải là số dương.")
                return
            try:
                vs = float(self.var_voice_speed.get().replace(",", "."))
                if vs <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("Số không hợp lệ", "Tốc độ voice phải là số dương (ví dụ 1, 1.2, 2).")
                return
            vsc = clamp_voice_speed(vs)
            if abs(vsc - vs) > 1e-6:
                messagebox.showwarning("Tốc độ voice", f"Giá trị ngoài 0,25–4; dùng {vsc}×.")
                self.var_voice_speed.set(str(vsc))
                s = self._gather_settings_from_ui()
            save_settings(s)
            self._settings = s
            self.var_progress.set(0)
            self.var_eta.set("Ước lượng: đang chuẩn bị…")
            self.btn_render.configure(state=tk.DISABLED)

            def worker_root() -> None:
                try:
                    self._run_root_pipeline(root, s, spi)
                finally:
                    self.after(0, lambda: self.btn_render.configure(state=tk.NORMAL))

            self._render_thread = threading.Thread(target=worker_root, daemon=True)
            self._render_thread.start()
            return

        imgdir = Path(s.image_dir)
        outdir = Path(s.output_dir)

        voices: List[Path] = []
        if s.use_voice_folder:
            vdir = Path(s.voice_folder)
            if not vdir.is_dir():
                messagebox.showerror("Thư mục voice", "Chọn thư mục chứa file voice hợp lệ.")
                return
            voices = list_audio_files(vdir)
            if not voices:
                messagebox.showerror(
                    "Thư mục voice",
                    "Không có file âm thanh hợp lệ trong thư mục (mp3, m4a, wav, aac, flac, ogg…).",
                )
                return
        else:
            voice = Path(s.voice_path)
            if not voice.is_file():
                messagebox.showerror("Thiếu file", "Chọn file voice hợp lệ.")
                return
            if voice.suffix.lower() != ".mp3":
                messagebox.showwarning("Định dạng", "Nên dùng file .mp3 theo yêu cầu.")
            voices = [voice]

        if not imgdir.is_dir():
            messagebox.showerror("Thiếu thư mục", "Chọn thư mục ảnh hợp lệ.")
            return
        if not outdir.is_dir():
            messagebox.showerror("Thiếu thư mục", "Chọn thư mục video đầu ra.")
            return

        try:
            spi = float(self.var_spi.get().replace(",", "."))
            if spi <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Số không hợp lệ", "Mỗi ảnh (giây) phải là số dương.")
            return

        try:
            vs = float(self.var_voice_speed.get().replace(",", "."))
            if vs <= 0:
                raise ValueError
        except ValueError:
            messagebox.showerror("Số không hợp lệ", "Tốc độ voice phải là số dương (ví dụ 1, 1.2, 2).")
            return
        vsc = clamp_voice_speed(vs)
        if abs(vsc - vs) > 1e-6:
            messagebox.showwarning("Tốc độ voice", f"Giá trị ngoài 0,25–4; dùng {vsc}×.")
            self.var_voice_speed.set(str(vsc))
            s = self._gather_settings_from_ui()

        save_settings(s)
        self._settings = s

        self.var_progress.set(0)
        self.var_eta.set("Ước lượng: đang chuẩn bị…")
        self.btn_render.configure(state=tk.DISABLED)

        def worker() -> None:
            try:
                self._run_pipeline(voices, imgdir, outdir, s, spi)
            finally:
                self.after(0, lambda: self.btn_render.configure(state=tk.NORMAL))

        self._render_thread = threading.Thread(target=worker, daemon=True)
        self._render_thread.start()

    def _slideshow_encode_round(
        self,
        ffmpeg: str,
        voice: Path,
        images: List[Path],
        out_path: Path,
        effective_dur: float,
        spi: float,
        w: int,
        h: int,
        v_enc: str,
        atempo_chain: Optional[str],
        log: Callable[[str], None],
        job_idx: int,
        n_jobs: int,
    ) -> bool:
        """Ghép timeline ảnh + voice → một file MP4. Tiến độ thanh: job_idx / n_jobs."""
        batch = n_jobs > 1
        timeline = build_timeline(images, effective_dur, spi)
        log(
            f"Timeline: {len(timeline)} đoạn ảnh (lặp ảnh theo thứ tự, mỗi ảnh tối đa {spi}s)."
        )
        log(f"File đầu ra: {out_path}")

        t0 = time.perf_counter()

        def on_ff_line(
            line: str,
            i: int = job_idx,
            ed: float = effective_dur,
            t_enc: float = t0,
        ) -> None:
            t = parse_ffmpeg_time_sec(line)
            if t is not None and ed > 0:
                sub = min(1.0, t / ed)
                if batch:
                    pct = 100.0 * (i + 0.25 + 0.75 * sub) / n_jobs
                else:
                    pct = min(99.0, 25.0 + sub * 75.0)
                self._set_progress(pct, ed, t_enc, encode_frac=sub)

        if not batch:
            self._set_progress(25, effective_dur, 0)

        log("Render FFmpeg…")
        ok = False
        with tempfile.TemporaryDirectory(prefix="vslideshow_") as tmp:
            concat_path = Path(tmp) / "concat.txt"
            try:
                write_concat_file(timeline, concat_path, log)
            except OSError as e:
                log(f"Lỗi ghi file concat: {e}")
                return False

            ok = run_ffmpeg(
                ffmpeg,
                concat_path,
                voice,
                out_path,
                w,
                h,
                log,
                on_line=on_ff_line,
                audio_atempo_chain=atempo_chain,
                video_encoder=v_enc,
            )

            if not ok and v_enc != "libx264":
                if out_path.is_file():
                    try:
                        out_path.unlink()
                    except OSError:
                        pass
                log("Encode GPU thất bại — thử lại bằng libx264 (CPU).")
                ok = run_ffmpeg(
                    ffmpeg,
                    concat_path,
                    voice,
                    out_path,
                    w,
                    h,
                    log,
                    on_line=on_ff_line,
                    audio_atempo_chain=atempo_chain,
                    video_encoder="libx264",
                )

        elapsed = time.perf_counter() - t0
        if ok:
            log(f"Xong ({elapsed:.1f}s) — {out_path}")
        else:
            log(f"Thất bại: {voice.name}")
        return ok

    def _run_pipeline(self, voices: List[Path], imgdir: Path, outdir: Path, s: AppSettings, spi: float) -> None:
        log = self._log
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        if not ffmpeg or not ffprobe:
            log("Lỗi: Không tìm thấy ffmpeg/ffprobe trong PATH. Cài FFmpeg và thêm vào PATH.")
            self.after(0, lambda: messagebox.showerror("FFmpeg", "Cần cài FFmpeg và thêm vào PATH."))
            return

        n_voices = len(voices)
        batch = n_voices > 1

        log("=== Bước 1/5: Kiểm tra công cụ ===")
        log(f"ffmpeg: {ffmpeg}")
        log(f"ffprobe: {ffprobe}")
        if batch:
            log(f"Chế độ thư mục voice: {n_voices} file — xử lý tuần tự.")
        self._set_progress(5, None, 0)

        log("=== Bước 2/5: Quét ảnh ===")
        images = list_images(imgdir)
        if not images:
            log("Không có ảnh (jpg/png/webp/…) trong thư mục.")
            self.after(0, lambda: messagebox.showerror("Ảnh", "Thư mục không có ảnh hợp lệ."))
            return
        log(f"Tìm thấy {len(images)} ảnh.")

        log("=== Bước 3/5: Kiểm tra kích thước ảnh ===")
        same, natural = analyze_images_same_size(images, log)
        if not same and natural is None:
            self.after(0, lambda: messagebox.showerror("Ảnh", "Không đọc được kích thước ảnh."))
            return

        w, h, desc = target_resolution(s.aspect, s.quality, same, natural)
        log(desc)
        w0, h0 = w, h
        w, h = ensure_even_dimensions(w, h)
        if (w, h) != (w0, h0):
            log(f"Kích thước encode (chẵn px, tốt cho GPU): {w0}x{h0} → {w}x{h}")

        enc_map = ffmpeg_available_video_encoders(ffmpeg)
        mode = s.video_encoder if s.video_encoder in ("libx264", "auto_gpu") else "auto_gpu"
        v_enc = resolve_encoder_choice(ffmpeg, mode, enc_map, w, h, log)

        speed = clamp_voice_speed(float(s.voice_speed))
        atempo_chain = build_atempo_chain(speed)
        if atempo_chain:
            log(f"Bộ lọc atempo (mọi file): {atempo_chain}")

        ok_count = 0
        failed: List[str] = []
        t0_all = time.perf_counter()

        for idx, voice in enumerate(voices):
            log(f"=== Voice {idx + 1}/{n_voices}: {voice.name} ===")

            log("Đo độ dài voice…")
            dur = ffprobe_duration_seconds(voice, ffprobe, log)
            if dur is None or dur <= 0:
                log(f"Bỏ qua (không đo được độ dài): {voice.name}")
                failed.append(voice.name)
                if batch:
                    self._set_progress(100.0 * (idx + 1) / n_voices, None, t0_all)
                continue

            effective_dur = dur / speed
            log(f"Độ dài file: {dur:.2f}s → khớp video (tốc độ {speed}×): {effective_dur:.2f}s")

            out_path = outdir / f"{voice.stem}.mp4"
            ok = self._slideshow_encode_round(
                ffmpeg,
                voice,
                images,
                out_path,
                effective_dur,
                spi,
                w,
                h,
                v_enc,
                atempo_chain,
                log,
                idx,
                n_voices,
            )
            if ok:
                ok_count += 1
            else:
                failed.append(voice.name)

            if batch:
                self._set_progress(100.0 * (idx + 1) / n_voices, None, t0_all)

        elapsed_all = time.perf_counter() - t0_all
        if batch:
            self._set_progress(100, None, t0_all, done=True)
            msg = f"Hoàn tất {ok_count}/{n_voices} video trong {elapsed_all:.0f}s."
            if failed:
                msg += f"\n\nLỗi / bỏ qua ({len(failed)}):\n" + "\n".join(failed[:20])
                if len(failed) > 20:
                    msg += f"\n… (+{len(failed) - 20} file)"
            if ok_count == n_voices:
                self.after(0, lambda m=msg: messagebox.showinfo("Xong", m))
            elif ok_count > 0:
                self.after(0, lambda m=msg: messagebox.showwarning("Một phần", m))
            else:
                self.after(0, lambda m=msg: messagebox.showerror("Lỗi", m))
            return

        if ok_count == 1:
            out_path = outdir / (voices[0].stem + ".mp4")
            log(f"Tổng thời gian: {elapsed_all:.1f}s — {out_path}")
            self._set_progress(100, None, t0_all, done=True)
            self.after(0, lambda p=out_path: messagebox.showinfo("Xong", f"Đã tạo:\n{p}"))
        else:
            log("Render thất bại.")
            self.var_eta.set("Ước lượng: —")
            detail = "\n".join(failed[:5]) if failed else ""
            extra = f"\n\n{detail}" if detail else ""
            self.after(
                0,
                lambda e=extra: messagebox.showerror("Lỗi", f"Không tạo được video.{e}"),
            )

    def _run_root_pipeline(self, root: Path, s: AppSettings, spi: float) -> None:
        log = self._log
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        if not ffmpeg or not ffprobe:
            log("Lỗi: Không tìm thấy ffmpeg/ffprobe trong PATH. Cài FFmpeg và thêm vào PATH.")
            self.after(0, lambda: messagebox.showerror("FFmpeg", "Cần cài FFmpeg và thêm vào PATH."))
            return

        subdirs = list_immediate_subdirs(root)
        if not subdirs:
            log("Không có thư mục con (một tầng) trong thư mục gốc.")
            self.after(
                0,
                lambda: messagebox.showerror("Thư mục gốc", "Thư mục không có thư mục con nào."),
            )
            return

        n_jobs = len(subdirs)
        log("=== Chế độ thư mục gốc ===")
        log(f"Gốc: {root}")
        log(f"{n_jobs} thư mục con (sắp theo tên).")
        self._set_progress(5, None, 0)

        speed = clamp_voice_speed(float(s.voice_speed))
        atempo_chain = build_atempo_chain(speed)
        if atempo_chain:
            log(f"Bộ lọc atempo (mọi dự án): {atempo_chain}")

        enc_map = ffmpeg_available_video_encoders(ffmpeg)
        mode = s.video_encoder if s.video_encoder in ("libx264", "auto_gpu") else "auto_gpu"

        ok_count = 0
        failed: List[str] = []
        t0_all = time.perf_counter()

        for idx, sub in enumerate(subdirs):
            log(f"=== Dự án {idx + 1}/{n_jobs}: {sub.name} ===")
            if dir_has_mp4_video(sub):
                log("Bỏ qua: thư mục con đã có file video .mp4 (giữ nguyên, không render lại).")
                failed.append(sub.name)
                self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)
                continue

            mp3s = list_mp3_files(sub)
            images = list_images(sub)

            if len(mp3s) != 1:
                if len(mp3s) == 0:
                    log("Bỏ qua: không có đúng một file .mp3 (0 file).")
                else:
                    log(f"Bỏ qua: có {len(mp3s)} file .mp3 (chỉ xử lý khi đúng 1).")
                failed.append(sub.name)
                self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)
                continue
            if not images:
                log("Bỏ qua: không có ảnh hợp lệ trong thư mục con.")
                failed.append(sub.name)
                self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)
                continue

            voice = mp3s[0]
            log(f"Dùng: {voice.name} + {len(images)} ảnh.")

            same, natural = analyze_images_same_size(images, log)
            if not same and natural is None:
                log("Bỏ qua: không đọc được kích thước ảnh.")
                failed.append(sub.name)
                self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)
                continue

            w, h, desc = target_resolution(s.aspect, s.quality, same, natural)
            log(desc)
            w0, h0 = w, h
            w, h = ensure_even_dimensions(w, h)
            if (w, h) != (w0, h0):
                log(f"Kích thước encode (chẵn px): {w0}x{h0} → {w}x{h}")

            v_enc = resolve_encoder_choice(ffmpeg, mode, enc_map, w, h, log)

            log("Đo độ dài voice…")
            dur = ffprobe_duration_seconds(voice, ffprobe, log)
            if dur is None or dur <= 0:
                log("Bỏ qua: không đo được độ dài audio.")
                failed.append(sub.name)
                self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)
                continue

            effective_dur = dur / speed
            log(f"Độ dài: {dur:.2f}s → video {effective_dur:.2f}s ({speed}×)")
            out_path = sub / f"{voice.stem}.mp4"

            ok = self._slideshow_encode_round(
                ffmpeg,
                voice,
                images,
                out_path,
                effective_dur,
                spi,
                w,
                h,
                v_enc,
                atempo_chain,
                log,
                idx,
                n_jobs,
            )
            if ok:
                ok_count += 1
            else:
                failed.append(sub.name)

            self._set_progress(100.0 * (idx + 1) / n_jobs, None, t0_all)

        elapsed_all = time.perf_counter() - t0_all
        self._set_progress(100, None, t0_all, done=True)
        msg = f"Hoàn tất {ok_count}/{n_jobs} dự án ({elapsed_all:.0f}s)."
        if failed:
            msg += f"\n\nBỏ qua / lỗi ({len(failed)}):\n" + "\n".join(failed[:20])
            if len(failed) > 20:
                msg += f"\n… (+{len(failed) - 20})"
        if ok_count == n_jobs:
            self.after(0, lambda m=msg: messagebox.showinfo("Xong", m))
        elif ok_count > 0:
            self.after(0, lambda m=msg: messagebox.showwarning("Một phần", m))
        else:
            self.after(0, lambda m=msg: messagebox.showerror("Lỗi", m))

    def _set_progress(
        self,
        pct: float,
        audio_dur: Optional[float],
        t0: float,
        done: bool = False,
        *,
        encode_frac: Optional[float] = None,
    ) -> None:
        """
        encode_frac: 0..1 tiến độ encode file hiện tại (từ dòng time= FFmpeg).
        Dùng cho ETA; batch mode không thể suy ra từ pct tổng (pct thường < 25).
        """

        def ui() -> None:
            self.var_progress.set(min(100.0, max(0.0, pct)))
            if done:
                self.var_eta.set("Xong")
                return
            if encode_frac is not None and t0 > 0:
                ef = min(1.0, max(0.0, encode_frac))
                if ef > 0.02:
                    elapsed = time.perf_counter() - t0
                    est_total = elapsed / ef
                    eta = max(0.0, est_total - elapsed)
                    self.var_eta.set(f"Ước lượng còn ~{eta:.0f}s")
                else:
                    self.var_eta.set("Ước lượng: đang encode…")
                return
            if audio_dur and pct > 25 and t0 > 0:
                enc_pct = (pct - 25) / 75.0
                if enc_pct > 0.02:
                    elapsed = time.perf_counter() - t0
                    est_total = elapsed / enc_pct
                    eta = max(0.0, est_total - elapsed)
                    self.var_eta.set(f"Ước lượng còn ~{eta:.0f}s")
                else:
                    self.var_eta.set("Ước lượng: đang encode…")
            elif pct <= 25:
                self.var_eta.set("Ước lượng: đang chuẩn bị / bắt đầu encode…")
            else:
                self.var_eta.set("Ước lượng: đang encode…")

        self.after(0, ui)


def _ensure_utf8_stdio() -> None:
    if sys.platform == "win32":
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (AttributeError, OSError, ValueError):
                pass


def main() -> None:
    _ensure_utf8_stdio()
    app = VideoEditorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
