# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import List, Tuple


SETTINGS_FILE = Path(__file__).resolve().parent / "video_editor_settings.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
AUDIO_EXTS = {".mp3", ".m4a", ".wav", ".aac", ".flac", ".ogg", ".opus"}
FOOTAGE_EXTS = {".mp4"}

ENCODER_OPTIONS: List[Tuple[str, str]] = [
    ("auto_gpu", "GPU - NVENC / QSV / AMF"),
    ("libx264", "CPU - libx264"),
]


@dataclass
class AppSettings:
    voice_path: str = ""
    image_dir: str = ""
    output_dir: str = ""
    seconds_per_image: float = 4.0
    voice_speed: float = 1.0
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

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "AppSettings":
        return cls(
            voice_path=str(data.get("voice_path", "")),
            image_dir=str(data.get("image_dir", "")),
            output_dir=str(data.get("output_dir", "")),
            seconds_per_image=float(data.get("seconds_per_image", 4.0)),
            voice_speed=float(data.get("voice_speed", 1.0)),
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
