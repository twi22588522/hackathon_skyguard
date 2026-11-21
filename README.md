# SkyGuard 2.0 — AI Aerial Search & Rescue Prototype

SkyGuard is a local Flask application for analysing aerial photographs with YOLO person detection. This upgrade adds multi-image mission workflows, a persistent SQLite mission log, configurable confidence thresholds, a dashboard, and a map for camera GPS metadata embedded in image EXIF.

## Features
- Upload up to 20 JPG, JPEG, PNG or WEBP images per mission (40 MB request limit).
- YOLO person detection (COCO class `person`) with adjustable confidence threshold.
- Annotated output images, confidence scores and image-relative observations.
- SQLite mission history and dashboard totals.
- Leaflet/OpenStreetMap map for camera GPS coordinates found in EXIF.
- Per-mission detail API and health endpoint.
- Safer default: Flask debug mode is off unless `FLASK_DEBUG=1`.

## Important mapping limitation
A GPS coordinate in image EXIF normally describes the camera/drone capture position. It does **not** locate a detected person on the ground. SkyGuard labels these as camera GPS pins. Images without EXIF GPS are not mapped. The current detector does not estimate person GPS coordinates, altitude, terrain position, or rescue priority.

## Run locally
Python 3.10–3.12 is recommended.

### Windows
```bat
py -3.11 -m venv .venv
.venv\Scripts\activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```
Open http://127.0.0.1:5000. The supplied `yolov8n.pt` file is used by default. If it is absent, set `SKYGUARD_MODEL` to a valid Ultralytics model path/name.

### macOS / Linux
```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python app.py
```

## API
- `GET /api/health` — backend/model status.
- `GET /api/missions` — latest 50 mission summaries.
- `GET /api/missions/<mission_id>` — mission and image analysis records.
- `POST /api/analyze` — multipart form fields:
  - `images`: one or more image files (repeat this field)
  - `mission_name`: optional mission name
  - `confidence`: threshold between `0.05` and `0.90`, e.g. `0.15`

## Storage
- Uploaded originals: `static/uploads/`
- Annotated results: `static/results/`
- Mission database: `skyguard.db`

For a demo, use non-sensitive sample imagery. Add retention controls, authentication, HTTPS, CSRF protection, and access controls before exposing the app to a network. Do not use detections as verified intelligence, medical triage, or dispatch instructions. Human review and field validation are required.
