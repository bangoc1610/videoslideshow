# -*- coding: utf-8 -*-
"""
Công cụ ghép ảnh + voice MP3 thành video (FFmpeg).
Yêu cầu: FFmpeg có trong PATH (https://ffmpeg.org/download.html).
"""

from __future__ import annotations

import sys
import queue
import threading
import time
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from app_settings import AppSettings, ENCODER_OPTIONS, load_settings, save_settings
from video_backend import (
    analyze_images_same_size,
    build_atempo_chain,
    build_footage_sequence,
    build_timeline,
    choose_random_footage_pool,
    clamp_voice_speed,
    dir_has_mp4_video,
    ensure_even_dimensions,
    ffmpeg_available_video_encoders,
    ffprobe_duration_seconds,
    ffprobe_video_size,
    find_ffmpeg,
    find_ffprobe,
    list_audio_files,
    list_footage_files,
    list_images,
    list_immediate_subdirs,
    list_mp3_files,
    move_source_to_backup,
    parse_ffmpeg_time_sec,
    resolve_encoder_choice,
    run_ffmpeg,
    run_ffmpeg_footage,
    target_resolution,
    write_concat_file,
)


class HoverTip:
    def __init__(self, widget: tk.Widget, text: str, delay_ms: int = 450) -> None:
        self.widget = widget
        self.text = text.strip()
        self.delay_ms = delay_ms
        self._after_id: Optional[str] = None
        self._tip: Optional[tk.Toplevel] = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event: tk.Event) -> None:
        if not self.text:
            return
        self._cancel_schedule()
        self._after_id = self.widget.after(self.delay_ms, self._show)

    def _cancel_schedule(self) -> None:
        if self._after_id is not None:
            self.widget.after_cancel(self._after_id)
            self._after_id = None

    def _show(self) -> None:
        self._after_id = None
        if self._tip is not None or not self.text:
            return
        x = self.widget.winfo_pointerx() + 14
        y = self.widget.winfo_pointery() + 14
        tip = tk.Toplevel(self.widget)
        tip.wm_overrideredirect(True)
        tip.wm_geometry(f"+{x}+{y}")
        label = tk.Label(
            tip,
            text=self.text,
            justify=tk.LEFT,
            bg="#FFF7D6",
            fg="#1F2937",
            relief=tk.SOLID,
            borderwidth=1,
            padx=8,
            pady=5,
            font=("Segoe UI", 9),
            wraplength=340,
        )
        label.pack()
        self._tip = tip

    def _hide(self, _event: Optional[tk.Event] = None) -> None:
        self._cancel_schedule()
        if self._tip is not None:
            self._tip.destroy()
            self._tip = None


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

    def _configure_styles(self) -> None:
        style = ttk.Style(self)
        style.configure("AppTitle.TLabel", font=("Segoe UI Semibold", 16))
        style.configure("Section.TLabelframe", padding=12)
        style.configure("Section.TLabelframe.Label", font=("Segoe UI Semibold", 10))
        style.configure("Muted.TLabelframe", padding=12)
        style.configure("Muted.TLabelframe.Label", font=("Segoe UI Semibold", 10), foreground="#8A8F98")
        style.configure("Hint.TLabel", foreground="#4B5563")
        style.configure("Muted.TLabel", foreground="#8A8F98")
        style.configure("Mode.TRadiobutton", font=("Segoe UI Semibold", 10))
        style.configure("ModeDesc.TLabel", foreground="#5B6570")
        style.configure("StatusKey.TLabel", foreground="#6B7280")
        style.configure("StatusValue.TLabel", font=("Segoe UI Semibold", 10))

    def _create_section(
        self,
        parent: ttk.Frame,
        key: str,
        title: str,
        row: int,
        column: int,
        tooltip: str,
    ) -> ttk.Frame:
        del row, column
        frame = ttk.LabelFrame(parent, text=title, style="Section.TLabelframe", padding=10)
        frame.pack(fill=tk.X, pady=(0, 10))
        status = ttk.Label(frame, style="Hint.TLabel")
        status.pack(anchor=tk.W, pady=(0, 6))
        body = ttk.Frame(frame)
        body.pack(fill=tk.X, expand=True)
        self._section_frames[key] = frame
        self._section_status[key] = status
        self._section_widgets[key] = []
        HoverTip(frame, tooltip)
        HoverTip(status, tooltip)
        return body

    def _register_section_widgets(self, key: str, *widgets: tk.Widget) -> None:
        self._section_widgets[key].extend(widgets)

    def _set_section_enabled(self, key: str, enabled: bool, active_text: str, inactive_text: str) -> None:
        frame = self._section_frames[key]
        status = self._section_status[key]
        frame.configure(style="Section.TLabelframe" if enabled else "Muted.TLabelframe")
        status.configure(
            text=active_text if enabled else inactive_text,
            style="Hint.TLabel" if enabled else "Muted.TLabel",
        )
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self._section_widgets[key]:
            try:
                widget.configure(state=state)
            except tk.TclError:
                pass

    def _mode_from_settings(self, s: AppSettings) -> str:
        if s.use_root_folder:
            return "root_batch"
        if s.use_voice_folder and s.use_footage_folder:
            return "batch_footage"
        if s.use_voice_folder:
            return "batch_image"
        return "single_image"

    def _on_sidebar_frame_configure(self, _event: tk.Event) -> None:
        self._sidebar_canvas.configure(scrollregion=self._sidebar_canvas.bbox("all"))

    def _on_sidebar_canvas_configure(self, event: tk.Event) -> None:
        self._sidebar_canvas.itemconfigure(self._sidebar_window, width=event.width)

    def _bind_sidebar_mousewheel(self, _event: Optional[tk.Event] = None) -> None:
        self.bind_all("<MouseWheel>", self._scroll_sidebar, add="+")
        self.bind_all("<Button-4>", self._scroll_sidebar, add="+")
        self.bind_all("<Button-5>", self._scroll_sidebar, add="+")

    def _unbind_sidebar_mousewheel(self, _event: Optional[tk.Event] = None) -> None:
        self.unbind_all("<MouseWheel>")
        self.unbind_all("<Button-4>")
        self.unbind_all("<Button-5>")

    def _scroll_sidebar(self, event: tk.Event) -> None:
        if getattr(event, "delta", 0):
            step = -1 * int(event.delta / 120) if event.delta else 0
        elif getattr(event, "num", None) == 4:
            step = -1
        elif getattr(event, "num", None) == 5:
            step = 1
        else:
            step = 0
        if step:
            self._sidebar_canvas.yview_scroll(step, "units")

    def _build_ui(self) -> None:
        self.geometry("1280x860")
        self.minsize(1040, 720)
        self._configure_styles()

        self._section_frames: Dict[str, ttk.LabelFrame] = {}
        self._section_status: Dict[str, ttk.Label] = {}
        self._section_widgets: Dict[str, List[tk.Widget]] = {}

        frm = ttk.Frame(self, padding=10)
        frm.pack(fill=tk.BOTH, expand=True)
        frm.columnconfigure(1, weight=1)
        frm.rowconfigure(1, weight=1)

        header = ttk.Frame(frm)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        ttk.Label(header, text="Ghep anh + voice thanh video", style="AppTitle.TLabel").pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Sidebar ben trai gom toan bo cai dat. Ben phai hien trang thai render va nhat ky.",
            style="Hint.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        left_shell = ttk.Frame(frm, width=470)
        left_shell.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        left_shell.grid_propagate(False)

        right_shell = ttk.Frame(frm)
        right_shell.grid(row=1, column=1, sticky="nsew")

        self.var_mode = tk.StringVar(value="single_image")
        self.var_voice = tk.StringVar()
        self.var_voice_folder = tk.StringVar()
        self.var_footage_folder = tk.StringVar()
        self.var_root_folder = tk.StringVar()
        self.var_imgdir = tk.StringVar()
        self.var_outdir = tk.StringVar()
        self.var_spi = tk.StringVar(value="4")
        self.var_random_footage_count = tk.StringVar(value="3")
        self.var_quality = tk.StringVar(value="1080")
        self.var_aspect = tk.StringVar(value="auto")
        self.var_voice_speed = tk.StringVar(value="1")
        self.var_enc_display = tk.StringVar()
        self.var_mode_status = tk.StringVar(value="-")
        self.var_mode_detail = tk.StringVar(value="Chon mode de bat dau.")
        self.var_run_state = tk.StringVar(value="San sang render.")
        self.var_latest_log = tk.StringVar(value="Chua co log.")

        settings_lf = ttk.LabelFrame(left_shell, text="Cai dat", style="Section.TLabelframe", padding=0)
        settings_lf.pack(fill=tk.BOTH, expand=True)

        sidebar_wrap = ttk.Frame(settings_lf)
        sidebar_wrap.pack(fill=tk.BOTH, expand=True)
        self._sidebar_canvas = tk.Canvas(sidebar_wrap, highlightthickness=0, borderwidth=0)
        sidebar_scroll = ttk.Scrollbar(sidebar_wrap, orient=tk.VERTICAL, command=self._sidebar_canvas.yview)
        self._sidebar_canvas.configure(yscrollcommand=sidebar_scroll.set)
        self._sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sidebar_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        sidebar = ttk.Frame(self._sidebar_canvas, padding=(10, 10, 10, 10))
        self._sidebar_window = self._sidebar_canvas.create_window((0, 0), window=sidebar, anchor=tk.NW)
        sidebar.bind("<Configure>", self._on_sidebar_frame_configure)
        self._sidebar_canvas.bind("<Configure>", self._on_sidebar_canvas_configure)
        for widget in (settings_lf, sidebar_wrap, self._sidebar_canvas, sidebar):
            widget.bind("<Enter>", self._bind_sidebar_mousewheel, add="+")
            widget.bind("<Leave>", self._unbind_sidebar_mousewheel, add="+")

        ttk.Label(
            sidebar,
            text="Nhap du lieu tu tren xuong duoi. Sidebar nay co the cuon khi danh sach cai dat dai hon man hinh.",
            style="Hint.TLabel",
            wraplength=400,
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=(0, 8))

        mode_lf = ttk.LabelFrame(sidebar, text="Loai chuc nang", style="Section.TLabelframe", padding=10)
        mode_lf.pack(fill=tk.X, pady=(0, 10))
        mode_lf.columnconfigure(0, weight=1)
        mode_lf.columnconfigure(1, weight=1)
        mode_items = [
            ("single_image", "1 voice + anh", "Chon 1 file MP3 va 1 thu muc anh de tao 1 video."),
            ("batch_image", "Thu muc voice + anh", "Lay tung file audio trong 1 thu muc va render chung bo anh."),
            ("batch_footage", "Thu muc voice + footage", "Lay tung MP3 trong thu muc, random N clip MP4 cho moi video."),
            ("root_batch", "Thu muc goc theo du an", "Moi thu muc con la 1 du an rieng: 1 MP3 + anh, video luu ngay trong thu muc con."),
        ]
        for idx, (mode_key, title, desc) in enumerate(mode_items):
            card = ttk.Frame(mode_lf, padding=(6, 4))
            card.grid(row=idx // 2, column=idx % 2, sticky="nsew", padx=4, pady=4)
            rb = ttk.Radiobutton(
                card,
                text=title,
                variable=self.var_mode,
                value=mode_key,
                command=self._sync_path_input_widgets,
                style="Mode.TRadiobutton",
            )
            rb.pack(anchor=tk.W)
            desc_lbl = ttk.Label(card, text=desc, style="ModeDesc.TLabel", wraplength=180, justify=tk.LEFT)
            desc_lbl.pack(anchor=tk.W, pady=(2, 0))
            HoverTip(rb, desc)
            HoverTip(desc_lbl, desc)

        single_body = self._create_section(sidebar, "single_voice", "Nguon voice don", 0, 0, "Dung cho che do 1 voice + anh. Chon 1 file audio nguon.")
        ttk.Label(single_body, text="File voice (MP3):").pack(side=tk.LEFT)
        self._entry_voice_file = ttk.Entry(single_body, textvariable=self.var_voice)
        self._entry_voice_file.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_voice_file = ttk.Button(single_body, text="Chon...", command=self._pick_voice)
        self._btn_voice_file.pack(side=tk.LEFT)
        self._register_section_widgets("single_voice", self._entry_voice_file, self._btn_voice_file)
        HoverTip(self._entry_voice_file, "Duong dan file MP3 dung cho che do 1 voice + anh.")

        batch_body = self._create_section(sidebar, "batch_voice", "Nguon voice batch", 1, 0, "Dung cho cac che do batch. Moi file audio trong thu muc se tao ra 1 video.")
        ttk.Label(batch_body, text="Thu muc voice:").pack(side=tk.LEFT)
        self._entry_voice_folder = ttk.Entry(batch_body, textvariable=self.var_voice_folder)
        self._entry_voice_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_voice_folder = ttk.Button(batch_body, text="Chon...", command=self._pick_voice_folder)
        self._btn_voice_folder.pack(side=tk.LEFT)
        self._register_section_widgets("batch_voice", self._entry_voice_folder, self._btn_voice_folder)
        HoverTip(self._entry_voice_folder, "Thu muc chua audio nguon. Mode footage se chi lay file .mp3.")

        root_body = self._create_section(sidebar, "root_batch", "Thu muc goc theo du an", 2, 0, "Moi thu muc con la 1 du an rieng. App se tim 1 MP3 va bo anh trong tung thu muc con.")
        ttk.Label(root_body, text="Thu muc goc:").pack(side=tk.LEFT)
        self._entry_root_folder = ttk.Entry(root_body, textvariable=self.var_root_folder)
        self._entry_root_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_root_folder = ttk.Button(root_body, text="Chon...", command=self._pick_root_folder)
        self._btn_root_folder.pack(side=tk.LEFT)
        self._register_section_widgets("root_batch", self._entry_root_folder, self._btn_root_folder)
        HoverTip(self._entry_root_folder, "Thu muc goc chua nhieu thu muc con, moi thu muc con se tao 1 video.")

        image_body = self._create_section(sidebar, "image_render", "Render bang anh", 0, 1, "Dung cho cac mode render bang anh. Cai dat nay khong ap dung cho mode footage MP4.")
        row_img = ttk.Frame(image_body)
        row_img.pack(fill=tk.X)
        ttk.Label(row_img, text="Thu muc anh:").pack(side=tk.LEFT)
        self._entry_imgdir = ttk.Entry(row_img, textvariable=self.var_imgdir)
        self._entry_imgdir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_imgdir = ttk.Button(row_img, text="Chon...", command=self._pick_imgdir)
        self._btn_imgdir.pack(side=tk.LEFT)
        row_spi = ttk.Frame(image_body)
        row_spi.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row_spi, text="Moi anh (giay):").pack(side=tk.LEFT)
        self._entry_spi = ttk.Entry(row_spi, textvariable=self.var_spi, width=8)
        self._entry_spi.pack(side=tk.LEFT, padx=6)
        ttk.Label(row_spi, text="Lap anh theo thu tu den khi het thoi luong audio.", style="Hint.TLabel").pack(side=tk.LEFT)
        self._register_section_widgets("image_render", self._entry_imgdir, self._btn_imgdir, self._entry_spi)
        HoverTip(self._entry_spi, "So giay hien thi toi da cho moi anh truoc khi chuyen sang anh tiep theo.")

        footage_body = self._create_section(sidebar, "footage_render", "Render bang footage MP4", 1, 1, "Dung cho mode thu muc voice + footage. Moi voice se random N clip MP4 tu thu muc nay.")
        row_footage = ttk.Frame(footage_body)
        row_footage.pack(fill=tk.X)
        ttk.Label(row_footage, text="Thu muc footage MP4:").pack(side=tk.LEFT)
        self._entry_footage_folder = ttk.Entry(row_footage, textvariable=self.var_footage_folder)
        self._entry_footage_folder.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_footage_folder = ttk.Button(row_footage, text="Chon...", command=self._pick_footage_folder)
        self._btn_footage_folder.pack(side=tk.LEFT)
        row_footage_count = ttk.Frame(footage_body)
        row_footage_count.pack(fill=tk.X, pady=(6, 0))
        ttk.Label(row_footage_count, text="So clip random / voice:").pack(side=tk.LEFT)
        self._entry_random_footage_count = ttk.Entry(row_footage_count, textvariable=self.var_random_footage_count, width=8)
        self._entry_random_footage_count.pack(side=tk.LEFT, padx=6)
        ttk.Label(row_footage_count, text="Neu tong thoi luong chua du, app se lap lai pool da random.", style="Hint.TLabel").pack(side=tk.LEFT)
        self._register_section_widgets("footage_render", self._entry_footage_folder, self._btn_footage_folder, self._entry_random_footage_count)
        HoverTip(self._entry_random_footage_count, "So clip MP4 random duoc boc cho moi file MP3.")

        output_body = self._create_section(sidebar, "output", "Thu muc xuat video", 2, 1, "Cac mode thong thuong xuat video vao thu muc nay. Mode thu muc goc se luu ngay trong tung thu muc con.")
        ttk.Label(output_body, text="Thu muc video ra:").pack(side=tk.LEFT)
        self._entry_outdir = ttk.Entry(output_body, textvariable=self.var_outdir)
        self._entry_outdir.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self._btn_outdir = ttk.Button(output_body, text="Chon...", command=self._pick_outdir)
        self._btn_outdir.pack(side=tk.LEFT)
        self._register_section_widgets("output", self._entry_outdir, self._btn_outdir)

        opt = ttk.LabelFrame(sidebar, text="Cai dat chung", style="Section.TLabelframe", padding=10)
        opt.pack(fill=tk.X, pady=(0, 10))

        common_top = ttk.Frame(opt)
        common_top.pack(fill=tk.X)
        ttk.Label(common_top, text="Chat luong:").pack(side=tk.LEFT)
        self.combo_quality = ttk.Combobox(common_top, textvariable=self.var_quality, values=("720", "1080", "2K", "4K"), state="readonly", width=8)
        self.combo_quality.pack(side=tk.LEFT, padx=6)
        ttk.Label(common_top, text="Ti le:").pack(side=tk.LEFT, padx=(16, 0))
        self.combo_aspect = ttk.Combobox(common_top, textvariable=self.var_aspect, values=("auto", "16:9", "9:16"), state="readonly", width=10)
        self.combo_aspect.pack(side=tk.LEFT, padx=6)

        common_mid = ttk.Frame(opt)
        common_mid.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(common_mid, text="Toc do voice (x):").pack(side=tk.LEFT)
        self._entry_voice_speed = ttk.Entry(common_mid, textvariable=self.var_voice_speed, width=8)
        self._entry_voice_speed.pack(side=tk.LEFT, padx=6)
        ttk.Label(common_mid, text="1 = giu nguyen, >1 nhanh hon, <1 cham hon.", style="Hint.TLabel").pack(side=tk.LEFT)

        common_bottom = ttk.Frame(opt)
        common_bottom.pack(fill=tk.X, pady=(8, 0))
        ttk.Label(common_bottom, text="Encode video:").pack(side=tk.LEFT)
        self.combo_enc = ttk.Combobox(common_bottom, textvariable=self.var_enc_display, values=[lbl for _eid, lbl in ENCODER_OPTIONS], state="readonly", width=52)
        self.combo_enc.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        HoverTip(self.combo_enc, "GPU thu NVENC/QSV/AMF truoc, neu khong hop le se fallback ve CPU.")

        footer = ttk.Label(sidebar, text="FFmpeg va ffprobe can co san trong PATH. Re chuot len cac muc de xem mo ta nhanh.", style="Hint.TLabel", wraplength=400, justify=tk.LEFT)
        footer.pack(anchor=tk.W, pady=(2, 6))
        HoverTip(footer, "Tooltip xuat hien khi re chuot len cac mode va mot so truong cai dat quan trong.")

        status_lf = ttk.LabelFrame(right_shell, text="Trang thai render", style="Section.TLabelframe", padding=12)
        status_lf.pack(fill=tk.X)

        btn_row = ttk.Frame(status_lf)
        btn_row.pack(fill=tk.X)
        self.btn_render = ttk.Button(btn_row, text="Bat dau render", command=self._start_render)
        self.btn_render.pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Luu cai dat", command=self._save_settings_clicked).pack(side=tk.LEFT, padx=8)

        status_grid = ttk.Frame(status_lf)
        status_grid.pack(fill=tk.X, pady=(12, 6))
        status_grid.columnconfigure(1, weight=1)
        ttk.Label(status_grid, text="Mode hien tai", style="StatusKey.TLabel").grid(row=0, column=0, sticky="nw", padx=(0, 10))
        ttk.Label(status_grid, textvariable=self.var_mode_status, style="StatusValue.TLabel").grid(row=0, column=1, sticky="nw")
        ttk.Label(status_grid, text="Mo ta", style="StatusKey.TLabel").grid(row=1, column=0, sticky="nw", padx=(0, 10), pady=(8, 0))
        ttk.Label(status_grid, textvariable=self.var_mode_detail, wraplength=520, justify=tk.LEFT).grid(row=1, column=1, sticky="nw", pady=(8, 0))
        ttk.Label(status_grid, text="Render", style="StatusKey.TLabel").grid(row=2, column=0, sticky="nw", padx=(0, 10), pady=(8, 0))
        ttk.Label(status_grid, textvariable=self.var_run_state, style="StatusValue.TLabel").grid(row=2, column=1, sticky="nw", pady=(8, 0))

        prog_row = ttk.Frame(status_lf)
        prog_row.pack(fill=tk.X, pady=(8, 0))
        self.var_progress = tk.DoubleVar(value=0)
        self.progress = ttk.Progressbar(prog_row, variable=self.var_progress, maximum=100)
        self.progress.pack(fill=tk.X, side=tk.LEFT, expand=True)
        self.var_eta = tk.StringVar(value="Uoc luong: -")
        ttk.Label(prog_row, textvariable=self.var_eta, width=28).pack(side=tk.RIGHT, padx=6)

        latest_row = ttk.Frame(status_lf)
        latest_row.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(latest_row, text="Su kien moi nhat", style="StatusKey.TLabel").pack(anchor=tk.W)
        ttk.Label(latest_row, textvariable=self.var_latest_log, wraplength=620, justify=tk.LEFT).pack(anchor=tk.W, pady=(2, 0))

        lf = ttk.LabelFrame(right_shell, text="Nhat ky", style="Section.TLabelframe", padding=8)
        lf.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        log_head = ttk.Frame(lf)
        log_head.pack(fill=tk.X, pady=(0, 6))
        ttk.Label(log_head, text="Toan bo log render se hien o day. Khung nay duoc giu o cot ben phai.", style="Hint.TLabel").pack(side=tk.LEFT)
        ttk.Button(log_head, text="Xoa log", command=self._clear_log).pack(side=tk.RIGHT)

        log_body = ttk.Frame(lf)
        log_body.pack(fill=tk.BOTH, expand=True)
        self.txt = tk.Text(log_body, height=24, wrap=tk.WORD, state=tk.DISABLED, font=("Consolas", 9))
        sb = ttk.Scrollbar(log_body, command=self.txt.yview)
        self.txt.configure(yscrollcommand=sb.set)
        self.txt.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        sb.pack(side=tk.RIGHT, fill=tk.Y)

    def _apply_settings_to_ui(self) -> None:
        s = self._settings
        self.var_mode.set(self._mode_from_settings(s))
        self.var_voice.set(s.voice_path)
        self.var_voice_folder.set(s.voice_folder)
        self.var_footage_folder.set(getattr(s, "footage_folder", ""))
        self.var_root_folder.set(s.root_folder)
        self.var_imgdir.set(s.image_dir)
        self.var_outdir.set(s.output_dir)
        self.var_spi.set(str(s.seconds_per_image))
        self.var_random_footage_count.set(str(max(1, int(getattr(s, "random_footage_count", 3)))))
        self.var_quality.set(s.quality if s.quality in ("720", "1080", "2K", "4K") else "1080")
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

    def _sync_path_input_widgets(self) -> None:
        mode = self.var_mode.get() or "single_image"
        single_mode = mode == "single_image"
        batch_footage_mode = mode == "batch_footage"
        root_mode = mode == "root_batch"
        image_mode = mode in {"single_image", "batch_image", "root_batch"}
        batch_mode = mode in {"batch_image", "batch_footage"}
        mode_text = {
            "single_image": "1 voice + anh",
            "batch_image": "Thu muc voice + anh",
            "batch_footage": "Thu muc voice + footage",
            "root_batch": "Thu muc goc theo du an",
        }
        mode_detail = {
            "single_image": "Chon 1 file MP3 va 1 thu muc anh. Video xuat ra thu muc da chi dinh.",
            "batch_image": "Duyet tung file audio trong thu muc voice va render bang bo anh chung.",
            "batch_footage": "Duyet tung file MP3 trong thu muc voice va random clip MP4 de lap video.",
            "root_batch": "Moi thu muc con la 1 du an rieng. Video duoc luu ngay trong thu muc con do.",
        }

        self.var_mode_status.set(mode_text.get(mode, "-"))
        self.var_mode_detail.set(mode_detail.get(mode, ""))

        self._set_section_enabled(
            "single_voice",
            single_mode,
            "Dang dung cho mode hien tai.",
            "Khong dung trong mode hien tai.",
        )
        self._set_section_enabled(
            "batch_voice",
            batch_mode,
            "Dang dung cho cac mode batch voice.",
            "Chi dung cho che do batch voice.",
        )
        self._set_section_enabled(
            "root_batch",
            root_mode,
            "Dang dung cho batch theo du an.",
            "Chi dung cho che do thu muc goc theo du an.",
        )
        self._set_section_enabled(
            "image_render",
            image_mode,
            "Dang dung cho render bang anh.",
            "Mode nay khong dung anh tinh.",
        )
        self._set_section_enabled(
            "footage_render",
            batch_footage_mode,
            "Dang dung cho render bang footage MP4.",
            "Chi dung cho mode thu muc voice + footage.",
        )
        self._set_section_enabled(
            "output",
            not root_mode,
            "Video se xuat vao thu muc nay.",
            "Mode nay luu video ngay trong tung thu muc con.",
        )

    def _encoder_id_from_display(self, display: str) -> str:
        for eid, lbl in ENCODER_OPTIONS:
            if lbl == display:
                return eid
        return "auto_gpu"

    def _gather_settings_from_ui(self) -> AppSettings:
        mode = self.var_mode.get() or "single_image"
        try:
            spi = float(self.var_spi.get().replace(",", "."))
        except ValueError:
            spi = 4.0
        try:
            vs = float(self.var_voice_speed.get().replace(",", "."))
        except ValueError:
            vs = 1.0
        try:
            random_footage_count = int(self.var_random_footage_count.get().strip())
        except ValueError:
            random_footage_count = 3
        return AppSettings(
            voice_path=self.var_voice.get().strip(),
            image_dir=self.var_imgdir.get().strip(),
            output_dir=self.var_outdir.get().strip(),
            seconds_per_image=spi,
            voice_speed=vs,
            video_encoder=self._encoder_id_from_display(self.var_enc_display.get()),
            quality=self.var_quality.get(),
            aspect=self.var_aspect.get(),
            use_voice_folder=mode in {"batch_image", "batch_footage"},
            voice_folder=self.var_voice_folder.get().strip(),
            use_footage_folder=mode == "batch_footage",
            footage_folder=self.var_footage_folder.get().strip(),
            random_footage_count=max(1, random_footage_count),
            use_root_folder=mode == "root_batch",
            root_folder=self.var_root_folder.get().strip(),
        )

    def _save_settings_clicked(self) -> None:
        self._settings = self._gather_settings_from_ui()
        save_settings(self._settings)
        self._log("Da luu cai dat vao video_editor_settings.json")

    def _pick_voice(self) -> None:
        if self.var_mode.get() != "single_image":
            return
        p = filedialog.askopenfilename(filetypes=[("MP3", "*.mp3"), ("Audio", "*.mp3 *.m4a *.wav"), ("All", "*.*")])
        if p:
            self.var_voice.set(p)

    def _pick_voice_folder(self) -> None:
        if self.var_mode.get() not in {"batch_image", "batch_footage"}:
            return
        p = filedialog.askdirectory()
        if p:
            self.var_voice_folder.set(p)

    def _pick_footage_folder(self) -> None:
        if self.var_mode.get() != "batch_footage":
            return
        p = filedialog.askdirectory()
        if p:
            self.var_footage_folder.set(p)

    def _pick_root_folder(self) -> None:
        if self.var_mode.get() != "root_batch":
            return
        p = filedialog.askdirectory()
        if p:
            self.var_root_folder.set(p)

    def _pick_imgdir(self) -> None:
        if self.var_mode.get() not in {"single_image", "batch_image", "root_batch"}:
            return
        p = filedialog.askdirectory()
        if p:
            self.var_imgdir.set(p)

    def _pick_outdir(self) -> None:
        if self.var_mode.get() == "root_batch":
            return
        p = filedialog.askdirectory()
        if p:
            self.var_outdir.set(p)

    def _log(self, msg: str) -> None:
        ts = time.strftime("%H:%M:%S")
        self._log_queue.put(f"[{ts}] {msg}")

    def _clear_log(self) -> None:
        self.txt.configure(state=tk.NORMAL)
        self.txt.delete("1.0", tk.END)
        self.txt.configure(state=tk.DISABLED)
        self.var_latest_log.set("Da xoa log hien thi.")

    def _drain_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self.txt.configure(state=tk.NORMAL)
                self.txt.insert(tk.END, line + "\n")
                self.txt.see(tk.END)
                self.txt.configure(state=tk.DISABLED)
                self.var_latest_log.set(line)
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
            self.var_run_state.set("Dang chuan bi render...")
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
        use_footage_mode = bool(s.use_voice_folder and s.use_footage_folder)

        voices: List[Path] = []
        if s.use_voice_folder:
            vdir = Path(s.voice_folder)
            if not vdir.is_dir():
                messagebox.showerror("Thư mục voice", "Chọn thư mục chứa file voice hợp lệ.")
                return
            voices = list_mp3_files(vdir) if use_footage_mode else list_audio_files(vdir)
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

        if use_footage_mode:
            footage_dir = Path(s.footage_folder)
            if not footage_dir.is_dir():
                messagebox.showerror("Footage MP4", "Chon thu muc footage MP4 hop le.")
                return
            if not list_footage_files(footage_dir):
                messagebox.showerror("Footage MP4", "Thu muc footage khong co file .mp4 hop le.")
                return
            try:
                footage_count = int(self.var_random_footage_count.get().strip())
                if footage_count <= 0:
                    raise ValueError
            except ValueError:
                messagebox.showerror("So clip footage", "So clip footage random phai la so nguyen duong.")
                return
        elif not imgdir.is_dir():
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
        self.var_run_state.set("Dang chuan bi render...")
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

    def _footage_encode_round(
        self,
        ffmpeg: str,
        voice: Path,
        footage_sequence: List[Path],
        out_path: Path,
        effective_dur: float,
        w: int,
        h: int,
        v_enc: str,
        atempo_chain: Optional[str],
        log: Callable[[str], None],
        job_idx: int,
        n_jobs: int,
    ) -> bool:
        batch = n_jobs > 1
        log(f"Footage sequence: {len(footage_sequence)} clip.")
        log(f"File dau ra: {out_path}")

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

        log("Render FFmpeg footage...")
        ok = run_ffmpeg_footage(
            ffmpeg,
            footage_sequence,
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
            log("Encode GPU that bai - thu lai bang libx264 (CPU).")
            ok = run_ffmpeg_footage(
                ffmpeg,
                footage_sequence,
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
            log(f"Xong ({elapsed:.1f}s) - {out_path}")
        else:
            log(f"That bai: {voice.name}")
        return ok

    def _run_footage_pipeline(self, voices: List[Path], outdir: Path, s: AppSettings) -> None:
        log = self._log
        ffmpeg = find_ffmpeg()
        ffprobe = find_ffprobe()
        if not ffmpeg or not ffprobe:
            log("Loi: Khong tim thay ffmpeg/ffprobe trong PATH. Cai FFmpeg va them vao PATH.")
            self.after(0, lambda: messagebox.showerror("FFmpeg", "Can cai FFmpeg va them vao PATH."))
            return

        footage_dir = Path(s.footage_folder)
        footages = list_footage_files(footage_dir)
        if not footages:
            log("Khong co footage .mp4 hop le trong thu muc.")
            self.after(0, lambda: messagebox.showerror("Footage MP4", "Thu muc footage khong co file .mp4 hop le."))
            return
        n_voices = len(voices)
        batch = n_voices > 1

        log("=== Buoc 1/5: Kiem tra cong cu ===")
        log(f"ffmpeg: {ffmpeg}")
        log(f"ffprobe: {ffprobe}")
        log(f"Footage: {len(footages)} file .mp4 trong {footage_dir}")
        if batch:
            log(f"Che do thu muc voice: {n_voices} file - xu ly tuan tu.")
        self._set_progress(5, None, 0)

        log("=== Buoc 2/5: Phan tich footage ===")
        natural = ffprobe_video_size(footages[0], ffprobe, log)
        w, h, desc = target_resolution(s.aspect, s.quality, True, natural)
        log(desc)
        w0, h0 = w, h
        w, h = ensure_even_dimensions(w, h)
        if (w, h) != (w0, h0):
            log(f"Kich thuoc encode (chan px, toi uu GPU): {w0}x{h0} -> {w}x{h}")

        enc_map = ffmpeg_available_video_encoders(ffmpeg)
        mode = s.video_encoder if s.video_encoder in ("libx264", "auto_gpu") else "auto_gpu"
        v_enc = resolve_encoder_choice(ffmpeg, mode, enc_map, w, h, log)

        speed = clamp_voice_speed(float(s.voice_speed))
        atempo_chain = build_atempo_chain(speed)
        if atempo_chain:
            log(f"Bo loc atempo (moi file): {atempo_chain}")

        random_count = max(1, int(s.random_footage_count))
        backup_dir = Path(s.voice_folder) / "backup"

        ok_count = 0
        failed: List[str] = []
        t0_all = time.perf_counter()

        for idx, voice in enumerate(voices):
            log(f"=== Voice {idx + 1}/{n_voices}: {voice.name} ===")
            dur = ffprobe_duration_seconds(voice, ffprobe, log)
            if dur is None or dur <= 0:
                log(f"Bo qua (khong do duoc do dai): {voice.name}")
                failed.append(voice.name)
                if batch:
                    self._set_progress(100.0 * (idx + 1) / n_voices, None, t0_all)
                continue

            effective_dur = dur / speed
            log(f"Do dai file: {dur:.2f}s -> video {effective_dur:.2f}s ({speed}x)")

            pool = choose_random_footage_pool(footages, random_count)
            if len(pool) < random_count:
                log(f"So footage yeu cau {random_count} > so file hien co {len(footages)} - dung {len(pool)} file.")
            log("Pool footage random: " + ", ".join(p.name for p in pool))

            durations: Dict[Path, float] = {}
            for clip in pool:
                clip_dur = ffprobe_duration_seconds(clip, ffprobe, log)
                if clip_dur is None or clip_dur <= 0:
                    log(f"Bo qua footage loi: {clip.name}")
                    continue
                durations[clip] = clip_dur

            footage_sequence = build_footage_sequence(pool, durations, effective_dur)
            if not footage_sequence:
                log("Khong the tao sequence footage hop le.")
                failed.append(voice.name)
                if batch:
                    self._set_progress(100.0 * (idx + 1) / n_voices, None, t0_all)
                continue

            total_footage = sum(durations.get(clip, 0.0) for clip in footage_sequence)
            log("Sequence footage: " + ", ".join(clip.name for clip in footage_sequence) + f" (tong ~{total_footage:.2f}s)")

            out_path = outdir / f"{voice.stem}.mp4"
            ok = self._footage_encode_round(
                ffmpeg,
                voice,
                footage_sequence,
                out_path,
                effective_dur,
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
                move_source_to_backup(voice, backup_dir, log)
            else:
                failed.append(voice.name)

            if batch:
                self._set_progress(100.0 * (idx + 1) / n_voices, None, t0_all)

        elapsed_all = time.perf_counter() - t0_all
        if batch:
            self._set_progress(100, None, t0_all, done=True)
            msg = f"Hoan tat {ok_count}/{n_voices} video trong {elapsed_all:.0f}s."
            if failed:
                msg += f"\n\nLoi / bo qua ({len(failed)}):\n" + "\n".join(failed[:20])
                if len(failed) > 20:
                    msg += f"\n... (+{len(failed) - 20} file)"
            if ok_count == n_voices:
                self.after(0, lambda m=msg: messagebox.showinfo("Xong", m))
            elif ok_count > 0:
                self.after(0, lambda m=msg: messagebox.showwarning("Mot phan", m))
            else:
                self.after(0, lambda m=msg: messagebox.showerror("Loi", m))
            return

        if ok_count == 1:
            out_path = outdir / (voices[0].stem + ".mp4")
            log(f"Tong thoi gian: {elapsed_all:.1f}s - {out_path}")
            self._set_progress(100, None, t0_all, done=True)
            self.after(0, lambda p=out_path: messagebox.showinfo("Xong", f"Da tao:\n{p}"))
        else:
            log("Render that bai.")
            self.var_eta.set("Uoc luong: -")
            detail = "\n".join(failed[:5]) if failed else ""
            extra = f"\n\n{detail}" if detail else ""
            self.after(
                0,
                lambda e=extra: messagebox.showerror("Loi", f"Khong tao duoc video.{e}"),
            )

    def _run_pipeline(self, voices: List[Path], imgdir: Path, outdir: Path, s: AppSettings, spi: float) -> None:
        log = self._log
        if s.use_voice_folder and s.use_footage_folder:
            self._run_footage_pipeline(voices, outdir, s)
            return
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
                self.var_run_state.set("Hoan tat render.")
                return
            if encode_frac is not None and t0 > 0:
                ef = min(1.0, max(0.0, encode_frac))
                if ef > 0.02:
                    elapsed = time.perf_counter() - t0
                    est_total = elapsed / ef
                    eta = max(0.0, est_total - elapsed)
                    self.var_eta.set(f"Ước lượng còn ~{eta:.0f}s")
                    self.var_run_state.set("Dang encode video...")
                else:
                    self.var_eta.set("Ước lượng: đang encode…")
                    self.var_run_state.set("Dang encode video...")
                return
            if audio_dur and pct > 25 and t0 > 0:
                enc_pct = (pct - 25) / 75.0
                if enc_pct > 0.02:
                    elapsed = time.perf_counter() - t0
                    est_total = elapsed / enc_pct
                    eta = max(0.0, est_total - elapsed)
                    self.var_eta.set(f"Ước lượng còn ~{eta:.0f}s")
                    self.var_run_state.set("Dang xu ly va encode...")
                else:
                    self.var_eta.set("Ước lượng: đang encode…")
                    self.var_run_state.set("Dang encode video...")
            elif pct <= 25:
                self.var_eta.set("Ước lượng: đang chuẩn bị / bắt đầu encode…")
                self.var_run_state.set("Dang quet du lieu va chuan bi render...")
            else:
                self.var_eta.set("Ước lượng: đang encode…")
                self.var_run_state.set("Dang encode video...")

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
