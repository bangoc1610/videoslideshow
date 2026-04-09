# Architecture

Current split:

- `video_slideshow_app.py`
  Frontend desktop app using Tkinter.
  Holds the window, widgets, UI state, and user interactions.

- `app_settings.py`
  Shared model/config layer.
  Contains `AppSettings`, encoder options, and JSON settings load/save.

- `video_backend.py`
  Backend service layer.
  Contains file scanning, image/audio helpers, FFmpeg/FFprobe helpers, timeline building, and render execution.

Suggested next step if you want a stricter MVC split:

1. Move render orchestration methods from `VideoEditorApp` into a controller class.
2. Keep Tkinter code in a dedicated `frontend/` package.
3. Keep FFmpeg pipeline logic in a dedicated `backend/` package.
4. Reserve `models/` for settings, render jobs, and status DTOs.
