from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import cv2
import numpy as np
from flask import Flask, jsonify, render_template, request, url_for
from PIL import Image, ImageDraw, ImageFont, ExifTags, UnidentifiedImageError
from werkzeug.utils import secure_filename
from ultralytics import YOLO

from thermal import detect_hotspots, fuse_with_people, thermal_notes

BASE_DIR = Path(__file__).resolve().parent
UPLOAD_DIR = BASE_DIR / "static" / "uploads"
RESULT_DIR = BASE_DIR / "static" / "results"
DB_PATH = BASE_DIR / "skyguard.db"
for folder in (UPLOAD_DIR, RESULT_DIR):
    folder.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.config.update(
    MAX_CONTENT_LENGTH=40 * 1024 * 1024,
    JSON_SORT_KEYS=False,
)
ALLOWED_EXTENSIONS = {"jpg", "jpeg", "png", "webp"}
MAX_IMAGE_PIXELS = 40_000_000
MODEL_NAME = os.getenv("SKYGUARD_MODEL", str(BASE_DIR / "yolov8n.pt"))
_model = None
_model_lock = threading.Lock()


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS missions (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                image_count INTEGER NOT NULL DEFAULT 0,
                detection_count INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'completed'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS analyses (
                id TEXT PRIMARY KEY,
                mission_id TEXT NOT NULL,
                filename TEXT NOT NULL,
                image_url TEXT NOT NULL,
                count INTEGER NOT NULL,
                detections_json TEXT NOT NULL,
                observations_json TEXT NOT NULL,
                width INTEGER NOT NULL,
                height INTEGER NOT NULL,
                camera_lat REAL,
                camera_lon REAL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(mission_id) REFERENCES missions(id)
            )
        """)


init_db()


def get_model():
    global _model
    if _model is None:
        with _model_lock:
            if _model is None:
                _model = YOLO(MODEL_NAME)
    return _model


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def region_name(cx, cy, width, height):
    horizontal = "left" if cx < width / 3 else "right" if cx > 2 * width / 3 else "centre"
    vertical = "upper" if cy < height / 3 else "lower" if cy > 2 * height / 3 else "middle"
    return f"{vertical} {horizontal}"


def _ratio_to_degrees(value):
    # Pillow may return IFDRational, tuple, or integer values.
    def number(v):
        try:
            return float(v)
        except (TypeError, ValueError, ZeroDivisionError):
            return float(v[0]) / float(v[1])
    return number(value[0]) + number(value[1]) / 60 + number(value[2]) / 3600


def extract_gps(image):
    """Return camera GPS from EXIF, if present. This is NOT a detected-person location."""
    try:
        exif = image.getexif()
        gps_ifd_id = next((k for k, v in ExifTags.TAGS.items() if v == "GPSInfo"), None)
        if not exif or gps_ifd_id is None:
            return None, None
        gps = exif.get_ifd(gps_ifd_id)
        if not gps:
            return None, None
        tags = {ExifTags.GPSTAGS.get(k, k): v for k, v in gps.items()}
        lat = _ratio_to_degrees(tags["GPSLatitude"])
        lon = _ratio_to_degrees(tags["GPSLongitude"])
        if tags.get("GPSLatitudeRef") == "S":
            lat = -lat
        if tags.get("GPSLongitudeRef") == "W":
            lon = -lon
        if -90 <= lat <= 90 and -180 <= lon <= 180:
            return round(lat, 7), round(lon, 7)
    except Exception:
        app.logger.info("No readable GPS EXIF metadata found")
    return None, None


def build_observations(detections):
    count = len(detections)
    if not count:
        return [
            "No people were detected in this image.",
            "A zero-detection result does not prove the area is clear; distance, occlusion, lighting and camera angle can affect detection.",
            "Review the original image and consider another flight pass or viewing angle."
        ]
    regions = {}
    for item in detections:
        regions[item["region"]] = regions.get(item["region"], 0) + 1
    notes = [f"{count} person detection{'s' if count != 1 else ''} found. A human reviewer should verify every bounding box."]
    peak = max(regions.values())
    busiest = [name for name, value in regions.items() if value == peak]
    if peak > 1:
        notes.append(f"Detection concentration: {', '.join(busiest)} image region(s).")
    elif count == 1:
        notes.append(f"One person-shaped detection appears in the {detections[0]['region']} image region.")
    else:
        notes.append("Detections are distributed across multiple image regions.")
    low = sum(d["confidence"] < 50 for d in detections)
    if low:
        notes.append(f"{low} detection(s) have a model score below 50%; inspect these carefully.")
    notes.append("Image regions are not GPS coordinates. Do not dispatch based on AI output alone.")
    return notes


def serialize_analysis(row):
    return {
        "id": row["id"], "mission_id": row["mission_id"],
        "filename": row["filename"], "image_url": row["image_url"],
        "count": row["count"], "detections": json.loads(row["detections_json"]),
        "observations": json.loads(row["observations_json"]),
        "image_width": row["width"], "image_height": row["height"],
        "camera_lat": row["camera_lat"], "camera_lon": row["camera_lon"],
        "created_at": row["created_at"],
    }


@app.route("/")
def index():
    return render_template("index.html")


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", "model": Path(MODEL_NAME).name, "database": "sqlite"})


@app.get("/api/missions")
def missions():
    with db() as conn:
        rows = conn.execute("SELECT * FROM missions ORDER BY created_at DESC LIMIT 50").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/missions/<mission_id>")
def mission_detail(mission_id):
    with db() as conn:
        mission = conn.execute("SELECT * FROM missions WHERE id=?", (mission_id,)).fetchone()
        if not mission:
            return jsonify(error="Mission not found."), 404
        rows = conn.execute("SELECT * FROM analyses WHERE mission_id=? ORDER BY created_at", (mission_id,)).fetchall()
    return jsonify({"mission": dict(mission), "analyses": [serialize_analysis(r) for r in rows]})


@app.patch("/api/analyses/<analysis_id>/detections/<int:detection_index>")
def update_detection_review(analysis_id, detection_index):
    data = request.get_json(silent=True) or {}
    status = data.get("status")

    if status not in {"confirmed", "rejected"}:
        return jsonify(error="Status must be confirmed or rejected."), 400

    with db() as conn:
        row = conn.execute(
            "SELECT detections_json FROM analyses WHERE id = ?",
            (analysis_id,)
        ).fetchone()

        if row is None:
            return jsonify(error="Analysis not found."), 404

        detections = json.loads(row["detections_json"])

        if detection_index < 0 or detection_index >= len(detections):
            return jsonify(error="Detection not found."), 404

        detections[detection_index]["review_status"] = status

        conn.execute(
            "UPDATE analyses SET detections_json = ? WHERE id = ?",
            (json.dumps(detections), analysis_id)
        )

    return jsonify({
        "success": True,
        "detection": detections[detection_index]
    })


@app.post("/api/analyze")
def analyze():
    files = request.files.getlist("images")
    if not files or all(not f.filename for f in files):
        return jsonify(error="Choose at least one aerial image."), 400
    if len(files) > 20:
        return jsonify(error="A mission can contain up to 20 images."), 400

    mission_name = (request.form.get("mission_name") or "Untitled rescue mission").strip()[:100]
    try:
        confidence_threshold = float(request.form.get("confidence", "0.15"))
    except ValueError:
        return jsonify(error="Confidence threshold must be a number."), 400
    if not 0.05 <= confidence_threshold <= 0.90:
        return jsonify(error="Confidence threshold must be between 0.05 and 0.90."), 400

    # --- detection mode / thermal settings ---
    mode = request.form.get("mode", "visible")
    if mode not in ("visible", "thermal", "both"):
        return jsonify(error="Mode must be visible, thermal or both."), 400
    palette = request.form.get("palette", "whitehot")
    if palette not in ("whitehot", "blackhot", "ironbow"):
        return jsonify(error="Unknown thermal palette."), 400
    try:
        heat_percentile = float(request.form.get("heat_percentile", "98"))
    except ValueError:
        return jsonify(error="Heat percentile must be a number."), 400
    heat_percentile = min(99.8, max(90.0, heat_percentile))

    valid_files = [f for f in files if f.filename]
    for f in valid_files:
        if not allowed_file(f.filename):
            return jsonify(error=f"Unsupported file type: {secure_filename(f.filename)}. Use JPG, PNG or WEBP."), 400

    mission_id = uuid4().hex
    created_at = datetime.now(timezone.utc).isoformat()
    saved_analyses = []
    try:
        with db() as conn:
            conn.execute(
                "INSERT INTO missions(id,name,created_at,image_count,detection_count,status) VALUES(?,?,?,?,?,?)",
                (mission_id, mission_name, created_at, 0, 0, "processing")
            )

        for file in valid_files:
            analysis_id = uuid4().hex
            safe_name = secure_filename(file.filename) or "aerial-image"
            upload_path = UPLOAD_DIR / f"{analysis_id}_{safe_name}"
            result_path = RESULT_DIR / f"{analysis_id}_detected.jpg"
            file.save(upload_path)

            try:
                with Image.open(upload_path) as source:
                    # Check the actual image format.
                    if source.format not in {"JPEG", "PNG", "WEBP"}:
                        raise ValueError(
                            f"{safe_name} has an unsupported image format."
                        )

                    width, height = source.size

                    # Reject invalid or oversized images.
                    if width <= 0 or height <= 0:
                        raise ValueError(
                            f"{safe_name} has invalid dimensions."
                        )

                    if width * height > MAX_IMAGE_PIXELS:
                        raise ValueError(
                            f"{safe_name} is too large. "
                            "Maximum allowed size is 40 megapixels."
                        )

                    source.verify()

                # Reopen after verify().
                with Image.open(upload_path) as source:
                    camera_lat, camera_lon = extract_gps(source)
                    image = source.convert("RGB")

            except (UnidentifiedImageError, OSError) as exc:
                upload_path.unlink(missing_ok=True)
                raise ValueError(
                    f"{safe_name} is not a readable image."
                ) from exc

            bgr = cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)   # clean copy, before drawing
            detections = []

            # --- visual person detection (YOLO, COCO class 0 = person) ---
            if mode in ("visible", "both"):
                model = get_model()
                with _model_lock:
                    results = model.predict(
                        source=str(upload_path), classes=[0], conf=confidence_threshold,
                        imgsz=1280, augment=True, verbose=False
                    )
                result = results[0]
                if result.boxes is not None:
                    for box in result.boxes:
                        x1, y1, x2, y2 = [float(v) for v in box.xyxy[0].tolist()]
                        score = float(box.conf[0])
                        detections.append({
                            "type": "person",
                            "confidence": round(score * 100, 1),
                            "priority": "HIGH" if score >= 0.8 else "MEDIUM" if score >= 0.5 else "LOW",
                            "region": region_name((x1 + x2) / 2, (y1 + y2) / 2, width, height),
                            "box": [round(x1), round(y1), round(x2), round(y2)],
                            "review_status": "pending",
                        })

            # --- thermal hotspot detection ---
            if mode in ("thermal", "both"):
                hot = detect_hotspots(bgr, palette=palette, percentile=heat_percentile)
                people = [d for d in detections if d["type"] == "person"]
                for t in fuse_with_people(hot, people):
                    # heat overlapping a visual person detection gets top priority
                    t["priority"] = "HIGH" if t["confirmed"] else "MEDIUM" if t["confidence"] >= 60 else "LOW"
                    t["review_status"] = "pending"
                    detections.append(t)

            # --- draw annotations ---
            draw = ImageDraw.Draw(image)
            try:
                font = ImageFont.truetype("arial.ttf", max(14, width // 90))
            except OSError:
                font = ImageFont.load_default()
            for d in detections:
                x1, y1, x2, y2 = d["box"]
                if d["type"] == "person":
                    colour, label = "#19d3a2", f"PERSON {d['confidence']:.0f}%"
                elif d["confirmed"]:
                    colour, label = "#ff2d2d", f"HEAT+PERSON {d['confidence']:.0f}"
                else:
                    colour, label = "#ff8a00", f"HEAT {d['confidence']:.0f}"
                draw.rectangle((x1, y1, x2, y2), outline=colour, width=max(3, width // 350))
                top = max(0, y1 - 25)
                draw.rectangle(draw.textbbox((x1, top), label, font=font), fill="#111111")
                draw.text((x1, top), label, fill="#ffffff", font=font)

            image.save(result_path, quality=92)
            image_url = url_for("static", filename=f"results/{result_path.name}")
            persons = [d for d in detections if d["type"] == "person"]
            observations = build_observations(persons) if mode != "thermal" else []
            observations += thermal_notes(detections, mode)
            record = {
                "id": analysis_id, "mission_id": mission_id, "filename": safe_name,
                "image_url": image_url, "count": len(detections),
                "detections": detections, "observations": observations,
                "image_width": width, "image_height": height,
                "camera_lat": camera_lat, "camera_lon": camera_lon,
                "created_at": datetime.now(timezone.utc).isoformat()
            }
            with db() as conn:
                conn.execute("""
                    INSERT INTO analyses VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                """, (
                    analysis_id, mission_id, safe_name, image_url, len(detections),
                    json.dumps(detections), json.dumps(observations), width, height,
                    camera_lat, camera_lon, record["created_at"]
                ))
            saved_analyses.append(record)

        total = sum(item["count"] for item in saved_analyses)
        with db() as conn:
            conn.execute(
                "UPDATE missions SET image_count=?, detection_count=?, status='completed' WHERE id=?",
                (len(saved_analyses), total, mission_id)
            )
        return jsonify({
            "mission": {"id": mission_id, "name": mission_name, "created_at": created_at},
            "image_count": len(saved_analyses), "detection_count": total,
            "analyses": saved_analyses,
            "message": "Analysis complete. Verify all detections before operational use."
        })
    except ValueError as exc:
        with db() as conn:
            conn.execute("UPDATE missions SET status='failed' WHERE id=?", (mission_id,))
        return jsonify(error=str(exc)), 400
    except Exception:
        app.logger.exception("Mission analysis failed")
        with db() as conn:
            conn.execute("UPDATE missions SET status='failed' WHERE id=?", (mission_id,))
        return jsonify(error="Analysis failed. Check the server terminal for details."), 500


@app.errorhandler(413)
def too_large(_error):
    return jsonify(error="Upload too large. The total request limit is 40 MB."), 413


if __name__ == "__main__":
    # Debug is opt-in for local development only.
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=os.getenv("FLASK_DEBUG") == "1")