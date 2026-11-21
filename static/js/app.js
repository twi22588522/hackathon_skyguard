let currentAnalyses = [];
let activeAnalysisIndex = 0;
let map, markersLayer;
let activePriorityFilter = "ALL";
let priorityFilterListenerAdded = false;

const $ = (id) => document.getElementById(id);

const fileInput = $("images");
const dropzone = $("dropzone");
const form = $("mission-form");
const confidence = $("confidence");
const modeEl = $("mode");
const paletteEl = $("palette");
const heatEl = $("heat-percentile");

function escapeHtml(value) {
  return String(value).replace(/[&<>"']/g, c => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;"
  }[c]));
}

// Older saved missions have no "type", so treat those as people.
function detectionKind(d) {
  if (d.type === "thermal") return d.confirmed ? "fused" : "heat";
  return "person";
}

// Count detections by kind for the summary line and map popups.
function summarise(detections) {
  const list = detections || [];
  const people = list.filter(d => detectionKind(d) === "person").length;
  const heat = list.filter(d => d.type === "thermal");
  const confirmed = heat.filter(d => d.confirmed).length;

  return {
    people,
    heat: heat.length,
    confirmed
  };
}

// Show thermal options only when needed.
function syncMode() {
  const mode = modeEl.value;

  $("thermal-options").classList.toggle("hidden", mode === "visible");
  $("confidence-row").classList.toggle("hidden", mode === "thermal");
  $("heat-percentile-value").textContent = heatEl.value;
}

// Initialize map.
function initMap() {
  if (!window.L) {
    $("map").innerHTML =
      '<div class="history-empty">Map library could not load. Check your internet connection.</div>';
    return;
  }

  map = L.map("map", {
    scrollWheelZoom: false
  }).setView([20.5937, 78.9629], 4);

  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution:
      '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
  }).addTo(map);

  markersLayer = L.layerGroup().addTo(map);
}

// Render camera GPS locations.
function renderMap(analyses) {
  if (!map) return;

  markersLayer.clearLayers();

  const points = analyses.filter(
    a => a.camera_lat != null && a.camera_lon != null
  );

  points.forEach(a => {
    const s = summarise(a.detections);

    const marker = L.marker([
      a.camera_lat,
      a.camera_lon
    ]).addTo(markersLayer);

    marker.bindPopup(
      `<b>${escapeHtml(a.filename)}</b><br>Camera GPS metadata<br>` +
      `${s.people} person detection(s)<br>` +
      `${s.heat} thermal hotspot(s)<br>` +
      `<small>Not person coordinates</small>`
    );
  });

  if (points.length) {
    map.fitBounds(
      L.latLngBounds(
        points.map(a => [a.camera_lat, a.camera_lon])
      ).pad(0.25),
      { maxZoom: 14 }
    );
  } else {
    map.setView([20.5937, 78.9629], 4);
  }
}

// Display selected analysis.
function showAnalysis(index) {
  const item = currentAnalyses[index];
  if (!item) return;

  activeAnalysisIndex = index;
  activePriorityFilter = "ALL";

  $("annotated-image").src =
    item.image_url + "?t=" + Date.now();

  const s = summarise(item.detections);

  const summary =
    `<div class="observation"><b>Summary:</b> ` +
    `<span style="color:#19d3a2">${s.people} person</span> · ` +
    `<span style="color:#ff8a00">${s.heat} heat</span> · ` +
    `<span style="color:#ff5a5a">${s.confirmed} heat+person</span></div>`;

  $("observations").innerHTML =
    summary +
    (item.observations || [])
      .map(x => `<div class="observation">${escapeHtml(x)}</div>`)
      .join("");

  renderReview(item);

  [...document.querySelectorAll(".result-tab")].forEach(
    (el, i) => el.classList.toggle("active", i === index)
  );
}

// Create priority filter controls.
function ensurePriorityFilterUI() {
  const reviewList = $("review-list");
  if (!reviewList) return;

  if (!priorityFilterListenerAdded) {
    document.addEventListener("click", event => {
      const button = event.target.closest("[data-priority-filter]");
      if (!button) return;

      activePriorityFilter =
        button.dataset.priorityFilter || "ALL";

      applyPriorityFilter();
    });

    priorityFilterListenerAdded = true;
  }

  if (document.querySelector("[data-priority-filter]")) return;

  let panel = $("priority-filter-panel");

  if (!panel) {
    panel = document.createElement("section");
    panel.id = "priority-filter-panel";

    panel.style.cssText =
      "margin:12px 0 16px;padding:14px;" +
      "border:1px solid rgba(148,163,184,.25);" +
      "border-radius:12px;background:rgba(15,23,42,.45);";

    panel.innerHTML = `
      <div style="display:flex;flex-wrap:wrap;gap:8px;margin-bottom:12px">

        <button type="button"
          data-priority-filter="HIGH"
          class="priority-filter-btn">
          HIGH
          <span id="priority-filter-high-count">0</span>
        </button>

        <button type="button"
          data-priority-filter="MEDIUM"
          class="priority-filter-btn">
          MEDIUM
          <span id="priority-filter-medium-count">0</span>
        </button>

        <button type="button"
          data-priority-filter="LOW"
          class="priority-filter-btn">
          LOW
          <span id="priority-filter-low-count">0</span>
        </button>

        <button type="button"
          data-priority-filter="ALL"
          class="priority-filter-btn"
          id="priority-show-all">
          SHOW ALL DETECTIONS
          <span id="priority-filter-total-count">0</span>
        </button>

      </div>

      <div aria-label="Confidence distribution"
        style="height:12px;display:flex;overflow:hidden;
        border-radius:99px;background:#334155">

        <div id="priority-high-bar"
          style="width:0%;background:#ef4444;transition:width .25s">
        </div>

        <div id="priority-medium-bar"
          style="width:0%;background:#f59e0b;transition:width .25s">
        </div>

        <div id="priority-low-bar"
          style="width:0%;background:#22c55e;transition:width .25s">
        </div>

      </div>

      <div style="display:flex;flex-wrap:wrap;gap:14px;
        margin-top:8px;font-size:12px;color:#cbd5e1">

        <span>HIGH <b id="priority-high-legend">0%</b></span>
        <span>MEDIUM <b id="priority-medium-legend">0%</b></span>
        <span>LOW <b id="priority-low-legend">0%</b></span>

      </div>
    `;

    reviewList.parentNode.insertBefore(panel, reviewList);

    const style = document.createElement("style");
    style.id = "priority-filter-styles";

    style.textContent = `
      .priority-filter-btn {
        border:1px solid rgba(148,163,184,.4);
        border-radius:8px;
        padding:8px 11px;
        background:#1e293b;
        color:#e2e8f0;
        font-weight:700;
        font-size:12px;
        cursor:pointer;
      }

      .priority-filter-btn:hover {
        filter:brightness(1.2);
      }

      .priority-filter-btn.active {
        outline:2px solid #38bdf8;
        background:#0c4a6e;
        color:#fff;
      }

      .priority-filter-btn span {
        display:inline-block;
        margin-left:5px;
        padding:1px 6px;
        border-radius:99px;
        background:rgba(255,255,255,.12);
      }

      .review-card.priority-filter-hidden {
        display:none!important;
      }
    `;

    document.head.appendChild(style);
  }
}

// Apply selected priority filter.
function applyPriorityFilter() {
  const cards = document.querySelectorAll(
    "#review-list .review-card"
  );

  cards.forEach(card => {
    const matches =
      activePriorityFilter === "ALL" ||
      card.dataset.priority === activePriorityFilter;

    card.classList.toggle(
      "priority-filter-hidden",
      !matches
    );
  });

  document.querySelectorAll("[data-priority-filter]").forEach(button => {
    const selected =
      (button.dataset.priorityFilter || "ALL") ===
      activePriorityFilter;

    button.classList.toggle("active", selected);

    button.setAttribute(
      "aria-pressed",
      String(selected)
    );
  });
}

// Update priority dashboard.
function updatePriorityDashboard(detections) {
  ensurePriorityFilterUI();

  const people = (detections || []).filter(
    d => detectionKind(d) === "person"
  );

  const getScore = d => {
    const raw = d.confidence ?? d.score ?? 0;
    const value = Number(raw);

    return Number.isFinite(value)
      ? (value <= 1 ? value * 100 : value)
      : 0;
  };

  const counts = {
    HIGH: people.filter(d => getScore(d) >= 80).length,

    MEDIUM: people.filter(
      d => getScore(d) >= 50 && getScore(d) < 80
    ).length,

    LOW: people.filter(d => getScore(d) < 50).length
  };

  const total = people.length;

  const setText = (id, value) => {
    const el = $(id);
    if (el) el.textContent = value;
  };

  setText("priority-high-count", counts.HIGH);
  setText("priority-medium-count", counts.MEDIUM);
  setText("priority-low-count", counts.LOW);
  setText("priority-total-count", total);

  setText("priority-filter-high-count", counts.HIGH);
  setText("priority-filter-medium-count", counts.MEDIUM);
  setText("priority-filter-low-count", counts.LOW);
  setText("priority-filter-total-count", total);

  const pct = key =>
    total ? (counts[key] / total * 100) : 0;

  ["HIGH", "MEDIUM", "LOW"].forEach(key => {
    const width = pct(key);

    const bar = $(
      "priority-" + key.toLowerCase() + "-bar"
    );

    if (bar) {
      bar.style.width = width + "%";
    }

    setText(
      "priority-" + key.toLowerCase() + "-legend",
      width.toFixed(1) + "%"
    );
  });

  applyPriorityFilter();
}

// Render human and thermal detections.
function renderReview(item) {
  const detections = item.detections || [];

  const personDetections = detections.filter(
    d => detectionKind(d) === "person"
  );

  const getScore = d => {
    const raw = Number(d.confidence ?? d.score ?? 0);
    return raw <= 1 ? raw * 100 : raw;
  };

  const highCount = personDetections.filter(
    d => getScore(d) >= 80
  ).length;

  const mediumCount = personDetections.filter(
    d => getScore(d) >= 50 && getScore(d) < 80
  ).length;

  const lowCount = personDetections.filter(
    d => getScore(d) < 50
  ).length;

  const highElement = $("priority-high-count");
  const mediumElement = $("priority-medium-count");
  const lowElement = $("priority-low-count");
  const totalElement = $("priority-total-count");

  if (highElement) highElement.textContent = highCount;
  if (mediumElement) mediumElement.textContent = mediumCount;
  if (lowElement) lowElement.textContent = lowCount;

  if (totalElement) {
    totalElement.textContent = personDetections.length;
  }

  if (!detections.length) {
    updatePriorityDashboard(detections);

    $("review-list").innerHTML =
      '<div class="review-empty">No detections available to review.</div>';

    return;
  }

  // Preserve original detection indexes for API requests.
  $("review-list").innerHTML = detections.map((d, i) => {
    const status = d.review_status || "pending";
    const kind = detectionKind(d);
    const score = getScore(d);

    const priority = d.priority || (
      kind === "fused" ? "HIGH" :
      score >= 80 ? "HIGH" :
      score >= 50 ? "MEDIUM" : "LOW"
    );

    const title =
      kind === "person" ? "Person detection" :
      kind === "fused" ? "Heat + person match" :
      "Heat signature";

    const badge =
      kind === "person"
        ? '<span class="type-badge type-person">VISUAL</span>'
        : kind === "fused"
        ? '<span class="type-badge type-fused">HEAT + PERSON</span>'
        : '<span class="type-badge type-heat">THERMAL</span>';

    const scoreText =
      kind === "person"
        ? `Confidence: ${score.toFixed(1)}%`
        : `Heat score: ${score.toFixed(0)}/100 (relative contrast)`;

    return `
      <div class="review-card is-${kind}"
        data-priority="${priority}"
        data-kind="${kind}">

        <div class="review-card-top">

          <div>
            <strong>${title} #${i + 1}</strong>
            ${badge}

            <div class="review-meta">
              ${scoreText}
              · Region: ${escapeHtml(d.region)}
              <br>
              Priority:
              <span class="priority-badge priority-${priority.toLowerCase()}">
                ${priority}
              </span>
            </div>
          </div>

          <span class="review-status ${status}">
            ${status.toUpperCase()}
          </span>

        </div>

        <div class="review-actions">

          <button class="confirm-btn"
            data-detection="${i}"
            data-status="confirmed"
            ${status !== "pending" ? "disabled" : ""}>
            CONFIRM
          </button>

          <button class="reject-btn"
            data-detection="${i}"
            data-status="rejected"
            ${status !== "pending" ? "disabled" : ""}>
            REJECT
          </button>

        </div>

      </div>
    `;
  }).join("");

  updatePriorityDashboard(detections);
}

// Handle CONFIRM and REJECT actions.
$("review-list").addEventListener("click", async event => {
  const button = event.target.closest(
    "button[data-detection]"
  );

  if (!button) return;

  const item = currentAnalyses[activeAnalysisIndex];
  if (!item) return;

  const detectionIndex = Number(button.dataset.detection);
  const status = button.dataset.status;

  if (
    !Number.isInteger(detectionIndex) ||
    !["confirmed", "rejected"].includes(status)
  ) {
    return;
  }

  button.disabled = true;

  try {
    const response = await fetch(
      `/api/analyses/${item.id}/detections/${detectionIndex}`,
      {
        method: "PATCH",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ status })
      }
    );

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.error || "Could not save review."
      );
    }

    item.detections[detectionIndex] = data.detection;

    renderReview(item);

  } catch (error) {
    alert(error.message);
    renderReview(item);
  }
});

// Render analysis results.
function renderResults(data) {
  currentAnalyses = data.analyses || [];

  $("empty-state").classList.add("hidden");
  $("results-content").classList.remove("hidden");

  $("result-count").textContent =
    data.detection_count ??
    currentAnalyses.reduce((n, a) => n + a.count, 0);

  $("result-images").textContent =
    data.image_count ?? currentAnalyses.length;

  $("result-tabs").innerHTML =
    currentAnalyses.map((a, i) => `
      <button class="result-tab ${i === 0 ? "active" : ""}"
        data-index="${i}">
        ${escapeHtml(a.filename)}
      </button>
    `).join("");

  $("result-tabs")
    .querySelectorAll("button")
    .forEach(btn => {
      btn.addEventListener("click", () =>
        showAnalysis(Number(btn.dataset.index))
      );
    });

  showAnalysis(0);
  renderMap(currentAnalyses);
}

// Update selected image list.
function updateFileList() {
  const files = [...fileInput.files];

  $("file-list").textContent = files.length
    ? `${files.length} selected · ${files.map(f => f.name).join(", ")}`
    : "No images selected";
}

fileInput.addEventListener("change", updateFileList);

confidence.addEventListener("input", () => {
  $("confidence-value").textContent =
    `${confidence.value}%`;
});

modeEl.addEventListener("change", syncMode);
heatEl.addEventListener("input", syncMode);

// Drag and drop functionality.
["dragenter", "dragover"].forEach(evt => {
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.add("dragover");
  });
});

["dragleave", "drop"].forEach(evt => {
  dropzone.addEventListener(evt, e => {
    e.preventDefault();
    dropzone.classList.remove("dragover");
  });
});

dropzone.addEventListener("drop", e => {
  const files = [...e.dataTransfer.files].filter(
    f => /\.(jpe?g|png|webp)$/i.test(f.name)
  );

  const transfer = new DataTransfer();

  files.forEach(f => transfer.items.add(f));

  fileInput.files = transfer.files;

  updateFileList();
});

// Submit mission for analysis.
form.addEventListener("submit", async e => {
  e.preventDefault();

  $("form-error").classList.add("hidden");

  if (!fileInput.files.length) {
    $("form-error").textContent =
      "Select at least one image.";

    $("form-error").classList.remove("hidden");
    return;
  }

  if (fileInput.files.length > 20) {
    $("form-error").textContent =
      "Select no more than 20 images.";

    $("form-error").classList.remove("hidden");
    return;
  }

  const payload = new FormData();

  payload.append(
    "mission_name",
    $("mission-name").value
  );

  payload.append(
    "confidence",
    (Number(confidence.value) / 100).toString()
  );

  payload.append("mode", modeEl.value);
  payload.append("palette", paletteEl.value);
  payload.append("heat_percentile", heatEl.value);

  [...fileInput.files].forEach(f =>
    payload.append("images", f)
  );

  $("analyse-btn").disabled = true;
  $("progress").classList.remove("hidden");

  try {
    const response = await fetch("/api/analyze", {
      method: "POST",
      body: payload
    });

    const data = await response.json();

    if (!response.ok) {
      throw new Error(
        data.error || "Analysis failed."
      );
    }

    renderResults(data);

    await loadDashboard();

    $("results-content").scrollIntoView({
      behavior: "smooth",
      block: "nearest"
    });

  } catch (err) {
    $("form-error").textContent = err.message;
    $("form-error").classList.remove("hidden");

  } finally {
    $("analyse-btn").disabled = false;
    $("progress").classList.add("hidden");
  }
});

// Load mission history and dashboard statistics.
async function loadDashboard() {
  try {
    const response = await fetch("/api/missions");
    const missions = await response.json();

    $("stat-missions").textContent = missions.length;

    $("stat-images").textContent =
      missions.reduce((n, m) => n + m.image_count, 0);

    $("stat-detections").textContent =
      missions.reduce((n, m) => n + m.detection_count, 0);

    const details = await Promise.all(
      missions.slice(0, 20).map(m =>
        fetch(`/api/missions/${m.id}`).then(r => r.json())
      )
    );

    const all = details.flatMap(
      d => d.analyses || []
    );

    $("stat-geotagged").textContent =
      all.filter(
        a => a.camera_lat != null && a.camera_lon != null
      ).length;

    $("mission-history").innerHTML = missions.length
      ? missions.map(m => `
          <div class="history-item" data-id="${m.id}">
            <b>${escapeHtml(m.name)}</b>
            <span>
              ${new Date(m.created_at).toLocaleString()}
              · ${m.image_count} image(s)
              · ${m.detection_count} detection(s)
            </span>
          </div>
        `).join("")
      : '<div class="history-empty">No saved missions yet. Run your first analysis.</div>';

    document.querySelectorAll(".history-item").forEach(el => {
      el.addEventListener("click", async () => {
        const d = await fetch(
          `/api/missions/${el.dataset.id}`
        ).then(r => r.json());

        if (d.analyses) {
          renderResults({
            analyses: d.analyses,
            image_count: d.mission.image_count,
            detection_count: d.mission.detection_count
          });
        }
      });
    });

  } catch (e) {
    console.warn("Dashboard refresh failed", e);
  }
}

$("refresh-history").addEventListener(
  "click",
  loadDashboard
);

// Application startup.
async function boot() {
  initMap();
  syncMode();

  try {
    const health = await fetch("/api/health").then(
      r => r.json()
    );

    $("engine-status").textContent =
      `Ready · ${health.model}`;

  } catch (e) {
    $("engine-status").textContent =
      "API unavailable";
  }

  await loadDashboard();
}

boot();