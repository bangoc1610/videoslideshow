# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable, List, Tuple


SETTINGS_FILE = Path(__file__).resolve().parent / "video_editor_settings.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}
FOOTAGE_EXTS = {".mp4"}

ENCODER_OPTIONS: List[Tuple[str, str]] = [
    ("auto_gpu", "GPU - NVENC / QSV / AMF"),
    ("libx264", "CPU - libx264"),
]

RENDER_PRESET_DEFAULT = "balance"
RENDER_PRESET_OPTIONS: List[Tuple[str, str]] = [
    ("fast", "Fast 🚀"),
    ("balance", "Balance ⚖️"),
    ("quality", "Quality 🎬"),
]


OVERLAY_POSITION_OPTIONS: List[Tuple[str, str]] = [
    ("top_left", "Tren trai"),
    ("top_center", "Tren giua"),
    ("top_right", "Tren phai"),
    ("center_left", "Giua trai"),
    ("center", "Chinh giua"),
    ("center_right", "Giua phai"),
    ("bottom_left", "Duoi trai"),
    ("bottom_center", "Duoi giua"),
    ("bottom_right", "Duoi phai"),
]

FASTER_WHISPER_MODEL_OPTIONS: List[Tuple[str, str]] = [
    ("tiny", "tiny - nhanh nhat"),
    ("base", "base - can bang"),
    ("small", "small - tot hon"),
    ("medium", "medium - chat luong cao"),
    ("large-v3", "large-v3 - rat cao"),
    ("distil-large-v3", "distil-large-v3 - nhanh/hieu qua"),
]

TRANSFORMATIVE_MOTION_EFFECTS: Tuple[str, ...] = (
    "zoom_in_light",
    "zoom_out_light",
    "pan_left_slow",
    "pan_right_slow",
    "pan_up_slow",
    "pan_down_slow",
    "flip_vertical_axis",
    "reverse_playback",
)

TRANSFORMATIVE_EDITORIAL_EFFECTS: Tuple[str, ...] = ()

TRANSFORMATIVE_FINISH_EFFECTS: Tuple[str, ...] = ()

TRANSFORMATIVE_EFFECT_OPTIONS: List[Tuple[str, str]] = [
    ("zoom_in_light", "Zoom in nhe"),
    ("zoom_out_light", "Zoom out nhe"),
    ("pan_left_slow", "Pan trai cham"),
    ("pan_right_slow", "Pan phai cham"),
    ("pan_up_slow", "Pan len cham"),
    ("pan_down_slow", "Pan xuong cham"),
    ("flip_vertical_axis", "Flip truc doc"),
    ("reverse_playback", "Reverse"),
]

TRANSFORMATIVE_EFFECT_KEYS = {effect_id for effect_id, _label in TRANSFORMATIVE_EFFECT_OPTIONS}
DEFAULT_TRANSFORMATIVE_EFFECTS: Tuple[str, ...] = ("zoom_in_light",)


def normalize_transformative_effects(values: Iterable[object]) -> List[str]:
    normalized: List[str] = []
    seen = set()
    for value in values:
        key = str(value or "").strip().lower()
        if key in TRANSFORMATIVE_EFFECT_KEYS and key not in seen:
            normalized.append(key)
            seen.add(key)
    return normalized


@dataclass
class AppSettings:
    voice_path: str = ""
    image_dir: str = ""
    output_dir: str = ""
    seconds_per_image: float = 4.0
    voice_speed: float = 1.0
    render_preset: str = RENDER_PRESET_DEFAULT
    video_encoder: str = "auto_gpu"
    quality: str = "1080"
    aspect: str = "auto"
    use_voice_folder: bool = False
    voice_folder: str = ""
    use_footage_folder: bool = False
    footage_folder: str = ""
    random_footage_count: int = 3
    use_root_folder: bool = False
    root_folder: str = ""
    cpu_threads: int = 0
    worker_count: int = 1
    logo_path: str = ""
    logo_size_pct: int = 0
    logo_position: str = "top_right"
    logo_margin_top: int = 24
    logo_margin_right: int = 24
    logo_margin_bottom: int = 24
    logo_margin_left: int = 24
    avatar_path: str = ""
    avatar_size_pct: int = 0
    avatar_position: str = "bottom_left"
    avatar_margin_top: int = 24
    avatar_margin_right: int = 24
    avatar_margin_bottom: int = 24
    avatar_margin_left: int = 24
    subscription_path: str = ""
    subscription_size_pct: int = 0
    subscription_position: str = "bottom_right"
    subscription_margin_top: int = 24
    subscription_margin_right: int = 24
    subscription_margin_bottom: int = 24
    subscription_margin_left: int = 24
    subscription_show_duration_sec: float = 3.0
    subscription_repeat_interval_sec: float = 10.0
    auto_subtitle_enabled: bool = False
    subtitle_model: str = "tiny"
    subtitle_position: str = "bottom_center"
    subtitle_font_name: str = "Arial"
    subtitle_font_size: int = 28
    subtitle_text_color: str = "#FFFFFF"
    subtitle_outline_width: float = 2.0
    subtitle_outline_color: str = "#000000"
    subtitle_background_color: str = ""
    subtitle_background_opacity: int = 35
    transformative_footage_enabled: bool = False
    transformative_effects: List[str] = field(default_factory=lambda: list(DEFAULT_TRANSFORMATIVE_EFFECTS))

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        raw_transformative_effects = data.get("transformative_effects", list(DEFAULT_TRANSFORMATIVE_EFFECTS))
        if isinstance(raw_transformative_effects, str):
            raw_transformative_effects = [item.strip() for item in raw_transformative_effects.split(",")]
        elif not isinstance(raw_transformative_effects, list):
            raw_transformative_effects = list(DEFAULT_TRANSFORMATIVE_EFFECTS)
        return cls(
            voice_path=str(data.get("voice_path", "")),
            image_dir=str(data.get("image_dir", "")),
            output_dir=str(data.get("output_dir", "")),
            seconds_per_image=float(data.get("seconds_per_image", 4.0)),
            voice_speed=float(data.get("voice_speed", 1.0)),
            render_preset=str(data.get("render_preset", RENDER_PRESET_DEFAULT)),
            video_encoder=str(data.get("video_encoder", "auto_gpu")),
            quality=str(data.get("quality", "1080")),
            aspect=str(data.get("aspect", "auto")),
            use_voice_folder=bool(data.get("use_voice_folder", False)),
            voice_folder=str(data.get("voice_folder", "")),
            use_footage_folder=bool(data.get("use_footage_folder", False)),
            footage_folder=str(data.get("footage_folder", "")),
            random_footage_count=max(1, int(data.get("random_footage_count", 3))),
            use_root_folder=bool(data.get("use_root_folder", False)),
            root_folder=str(data.get("root_folder", "")),
            cpu_threads=max(0, int(data.get("cpu_threads", data.get("ffmpeg_threads", 0)))),
            worker_count=max(1, int(data.get("worker_count", 1))),
            logo_path=str(data.get("logo_path", "")),
            logo_size_pct=max(0, min(100, int(data.get("logo_size_pct", 0)))),
            logo_position=str(data.get("logo_position", "top_right")),
            logo_margin_top=max(0, int(data.get("logo_margin_top", 24))),
            logo_margin_right=max(0, int(data.get("logo_margin_right", 24))),
            logo_margin_bottom=max(0, int(data.get("logo_margin_bottom", 24))),
            logo_margin_left=max(0, int(data.get("logo_margin_left", 24))),
            avatar_path=str(data.get("avatar_path", "")),
            avatar_size_pct=max(0, min(100, int(data.get("avatar_size_pct", 0)))),
            avatar_position=str(data.get("avatar_position", "bottom_left")),
            avatar_margin_top=max(0, int(data.get("avatar_margin_top", 24))),
            avatar_margin_right=max(0, int(data.get("avatar_margin_right", 24))),
            avatar_margin_bottom=max(0, int(data.get("avatar_margin_bottom", 24))),
            avatar_margin_left=max(0, int(data.get("avatar_margin_left", 24))),
            subscription_path=str(data.get("subscription_path", "")),
            subscription_size_pct=max(0, min(100, int(data.get("subscription_size_pct", 0)))),
            subscription_position=str(data.get("subscription_position", "bottom_right")),
            subscription_margin_top=max(0, int(data.get("subscription_margin_top", 24))),
            subscription_margin_right=max(0, int(data.get("subscription_margin_right", 24))),
            subscription_margin_bottom=max(0, int(data.get("subscription_margin_bottom", 24))),
            subscription_margin_left=max(0, int(data.get("subscription_margin_left", 24))),
            subscription_show_duration_sec=max(0.1, float(data.get("subscription_show_duration_sec", 3.0))),
            subscription_repeat_interval_sec=max(0.1, float(data.get("subscription_repeat_interval_sec", 10.0))),
            auto_subtitle_enabled=bool(data.get("auto_subtitle_enabled", False)),
            subtitle_model=str(data.get("subtitle_model", "tiny")).strip() or "tiny",
            subtitle_position=str(data.get("subtitle_position", "bottom_center")).strip() or "bottom_center",
            subtitle_font_name=str(data.get("subtitle_font_name", "Arial")).strip() or "Arial",
            subtitle_font_size=max(10, int(data.get("subtitle_font_size", 28))),
            subtitle_text_color=str(data.get("subtitle_text_color", "#FFFFFF")).strip() or "#FFFFFF",
            subtitle_outline_width=max(0.0, float(data.get("subtitle_outline_width", 2.0))),
            subtitle_outline_color=str(data.get("subtitle_outline_color", "#000000")).strip() or "#000000",
            subtitle_background_color=str(data.get("subtitle_background_color", "")).strip(),
            subtitle_background_opacity=max(0, min(100, int(data.get("subtitle_background_opacity", 35)))),
            transformative_footage_enabled=bool(data.get("transformative_footage_enabled", False)),
            transformative_effects=normalize_transformative_effects(raw_transformative_effects) or list(DEFAULT_TRANSFORMATIVE_EFFECTS),
        )


def load_settings() -> AppSettings:
    if SETTINGS_FILE.is_file():
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as file_obj:
                data = json.load(file_obj)
            return AppSettings.from_dict(data)
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            pass
    return AppSettings()


def save_settings(settings: AppSettings) -> None:
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as file_obj:
            json.dump(settings.to_dict(), file_obj, ensure_ascii=False, indent=2)
    except OSError:
        pass
