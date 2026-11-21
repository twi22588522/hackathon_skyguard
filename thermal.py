from __future__ import annotations
import cv2
import numpy as np

# Ignore the drone app's on-screen overlay (status bar, minimap, temperature scale).
# Set to False if your images are raw thermal frames with no overlay.
IGNORE_HUD = True

# (x0, y0, x1, y1) as fractions of the image. Tuned for DJI-style overlays.
HUD_ZONES = [
    (0.00, 0.00, 1.00, 0.14),   # top status bar
    (0.00, 0.68, 0.20, 1.00),   # mini-map
    (0.88, 0.10, 1.00, 0.92),   # temperature scale
    (0.78, 0.88, 1.00, 1.00),   # mode label
]


def _region(cx, cy, w, h):
    horizontal = "left" if cx < w / 3 else "right" if cx > 2 * w / 3 else "centre"
    vertical = "upper" if cy < h / 3 else "lower" if cy > 2 * h / 3 else "middle"
    return f"{vertical} {horizontal}"


def _valid_area(gray):
    """1 where we are allowed to look for people, 0 on black borders and the overlay."""
    h, w = gray.shape
    valid = np.zeros((h, w), np.uint8)

    cols = np.where(gray.max(axis=0) > 20)[0]
    rows = np.where(gray.max(axis=1) > 20)[0]
    if cols.size and rows.size:
        x0, x1, y0, y1 = int(cols[0]), int(cols[-1]) + 1, int(rows[0]), int(rows[-1]) + 1
    else:
        x0, x1, y0, y1 = 0, w, 0, h
    valid[y0:y1, x0:x1] = 1

    if IGNORE_HUD:
        cw, ch = x1 - x0, y1 - y0
        for fx0, fy0, fx1, fy1 in HUD_ZONES:
            valid[y0 + int(fy0 * ch): y0 + int(fy1 * ch),
                  x0 + int(fx0 * cw): x0 + int(fx1 * cw)] = 0
    return valid


def detect_hotspots(bgr, palette="whitehot", percentile=98.0,
                    min_area_px=25, max_area_frac=0.004,
                    min_brightness=180, min_score=40.0, max_results=15):
    """Find small, bright, compact heat blobs that could be people.

    palette: 'whitehot' / 'ironbow' (bright = hot) or 'blackhot' (dark = hot).
    percentile: higher = stricter.
    min_brightness: pixels dimmer than this (0-255 after palette fix) are never hot enough.
    """
    h, w = bgr.shape[:2]
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if palette == "blackhot":
        gray = 255 - gray
    gray = cv2.GaussianBlur(gray, (5, 5), 0)

    valid = _valid_area(gray)
    roi = gray[valid > 0]
    if roi.size == 0:
        return []

    thr = max(float(np.percentile(roi, percentile)),
              float(roi.mean() + 1.5 * roi.std()),
              float(min_brightness))
    mask = ((gray >= thr) & (valid > 0)).astype(np.uint8) * 255
    kernel = np.ones((3, 3), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    max_area = max_area_frac * w * h
    found = []
    for i in range(1, n):
        x, y, bw, bh, area = [int(v) for v in stats[i]]
        if area < min_area_px or area > max_area:
            continue                                   # too small (noise) or too big (rock, building)
        ratio = max(bw, bh) / max(1, min(bw, bh))
        if ratio > 4:
            continue                                   # long thin shapes: edges, roads, text
        if area / float(bw * bh) < 0.30:
            continue                                   # scattered / hollow shapes

        pad = max(bw, bh)
        x0, y0 = max(0, x - pad), max(0, y - pad)
        x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
        patch = gray[y0:y1, x0:x1]
        inside = labels[y0:y1, x0:x1] == i
        background = patch[~inside]
        if background.size == 0:
            continue
        contrast = (patch[inside].mean() - background.mean()) / 255.0
        score = round(min(100.0, contrast * 200), 1)
        if score < min_score:
            continue                                   # not clearly hotter than its surroundings

        found.append({
            "type": "thermal",
            "confidence": score,                       # heat score, not a probability
            "region": _region(x + bw / 2, y + bh / 2, w, h),
            "box": [x, y, x + bw, y + bh],
            "confirmed": False,
        })
    found.sort(key=lambda d: d["confidence"], reverse=True)
    return found[:max_results]


def fuse_with_people(thermal, people):
    """Mark hotspots whose centre lies inside a YOLO person box."""
    for t in thermal:
        cx = (t["box"][0] + t["box"][2]) / 2
        cy = (t["box"][1] + t["box"][3]) / 2
        for p in people:
            x1, y1, x2, y2 = p["box"]
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                t["confirmed"] = True
                break
    return thermal


def thermal_notes(detections, mode):
    hot = [d for d in detections if d["type"] == "thermal"]
    if mode == "visible":
        return []
    if not hot:
        return ["No person-sized heat signatures met the threshold. Try a lower heat strictness (95 to 97) or check the palette setting."]
    confirmed = sum(d["confirmed"] for d in hot)
    notes = [f"{len(hot)} person-sized heat signature(s) found. Heat score is relative contrast, not a temperature."]
    if confirmed:
        notes.append(f"{confirmed} signature(s) overlap a visual person detection (higher priority for review).")
    notes.append("Sun-warmed rock, vehicles, engines and animals can also look like this. Verify every signature manually.")
    return notes