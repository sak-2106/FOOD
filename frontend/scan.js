let currentUser = null;

const byId = (id) => document.getElementById(id);
const scanStatus = () => byId("scanStatus");

// What's been picked (or captured) and is waiting for "Use this photo".
let pending = { file: null, source: null, thumb: "", url: null };

// The analysis request currently in flight, if any.
let activeRequest = null; // { xhr, elapsedTimer, startedAt }

const SCAN_STAGES = [
  { key: "upload", label: "Uploading" },
  { key: "analyse", label: "Reading & explaining" },
  { key: "finish", label: "Preparing result" }
];

document.addEventListener("DOMContentLoaded", () => {
  currentUser = requireAuth();
  if (!currentUser) return; // redirected to login

  plantProduce();
  wireLogout();
  wireBrandHome();

  byId("userPill").textContent = currentUser.name || currentUser.email;

  byId("backToDashFromScan").addEventListener("click", () => {
    window.location.href = "dashboard.html";
  });

  wireTabs();
  wireDropzone();
  wireInputs();
  wirePreview();
});

// Coming back with the browser's Back button can restore this page frozen
// mid-scan. Start from a clean slate instead.
window.addEventListener("pageshow", (e) => {
  if (e.persisted) resetScanUI();
});

// ============================================================
// SCAN INPUT TABS
// ============================================================

function selectInputTab(name, { focus = false } = {}) {
  document.querySelectorAll(".tab-btn").forEach((t) => {
    const on = t.dataset.tab === name;
    t.classList.toggle("active", on);
    t.setAttribute("aria-selected", on ? "true" : "false");
    t.tabIndex = on ? 0 : -1;
    if (on && focus) t.focus();
  });
  document.querySelectorAll(".input-panel").forEach((p) => {
    p.classList.toggle("active", p.dataset.panel === name);
  });

  // Leaving the camera tab should always let go of the camera — it
  // shouldn't keep recording in the background.
  if (name !== "scan") stopCameraStream();
}

function wireTabs() {
  const tabs = [...document.querySelectorAll(".tab-btn")];

  tabs.forEach((tab) => {
    tab.addEventListener("click", () => selectInputTab(tab.dataset.tab));
    tab.addEventListener("keydown", (e) => {
      const idx = tabs.indexOf(tab);
      let next = null;
      if (e.key === "ArrowRight") next = tabs[(idx + 1) % tabs.length];
      else if (e.key === "ArrowLeft") next = tabs[(idx - 1 + tabs.length) % tabs.length];
      if (next) {
        e.preventDefault();
        selectInputTab(next.dataset.tab, { focus: true });
      }
    });
  });
}

// ============================================================
// DROPZONE
// ============================================================

function wireDropzone() {
  const dropzone = byId("dropzone");
  if (!dropzone) return;

  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("drag-over");
    })
  );

  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("drag-over");
    })
  );

  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer.files[0];
    if (file) handleChosenFile(file, "upload");
  });
}

// ============================================================
// INPUT WIRING
// ============================================================

function wireInputs() {
  byId("imageInput").addEventListener("change", (e) => {
    const file = e.target.files[0];
    e.target.value = ""; // so picking the same file again still fires "change"
    if (file) handleChosenFile(file, "upload");
  });

  wireCamera();
}

function handleChosenFile(file, source) {
  const looksLikeImage = (file.type || "").startsWith("image/") || /\.(jpe?g|png|webp|heic|heif)$/i.test(file.name || "");
  if (!looksLikeImage) {
    showScanError("That file isn't a photo. Use a JPG, PNG or WEBP of the label.");
    return;
  }
  showPreview(file, source);
}

// ============================================================
// FRAME ANALYSIS — shared by live camera guidance and the preview check
// ============================================================
//
// Cheap, on-device checks on a small copy of the image. They exist to
// nudge people ("Too dark", "Move closer") *before* a slow analysis run is
// wasted on a photo that can't be read. They are hints, not verdicts: the
// thresholds below are deliberately forgiving so a usable photo is never
// blocked, and they may want tuning against real phone cameras.

const EDGE_STEP = 40;        // gradient size that counts as a "real" edge (0–255 scale)
const DARK_BELOW = 55;       // average brightness under this reads as too dark
const GLARE_ABOVE = 0.3;     // share of blown-out pixels above this reads as glare
const DETAIL_MIN = 0.02;     // share of edge pixels under this: blurry, blank or too far
const MOTION_ABOVE = 55;     // texture-scaled change between checks that reads as movement (≈ 3 px of drift)

function analyseFrame(imageData, prevGray) {
  const { data, width: w, height: h } = imageData;
  const n = w * h;
  const gray = new Uint8Array(n);

  let sum = 0;
  let blown = 0;
  for (let i = 0, j = 0; i < n; i++, j += 4) {
    const g = (data[j] * 77 + data[j + 1] * 150 + data[j + 2] * 29) >> 8;
    gray[i] = g;
    sum += g;
    if (g >= 250) blown++;
  }

  // edge density in the middle 60% — that's where the label should be
  const x0 = Math.max(1, Math.floor(w * 0.2));
  const x1 = Math.min(w - 1, Math.floor(w * 0.8));
  const y0 = Math.max(1, Math.floor(h * 0.2));
  const y1 = Math.min(h - 1, Math.floor(h * 0.8));
  let edges = 0;
  let cells = 0;
  for (let y = y0; y < y1; y++) {
    const row = y * w;
    for (let x = x0; x < x1; x++) {
      const gx = gray[row + x + 1] - gray[row + x - 1];
      const gy = gray[row + w + x] - gray[row - w + x];
      if (Math.abs(gx) + Math.abs(gy) > EDGE_STEP) edges++;
      cells++;
    }
  }

  // Motion is judged on 4×4 block averages, not raw pixels: a hand drifting
  // a pixel or two between checks shouldn't count as "moving" (sharp text
  // makes raw differences balloon), but a real swing across the label should.
  const pw = Math.floor(w / 4);
  const ph = Math.floor(h / 4);
  const pooled = new Uint8Array(pw * ph);
  for (let by = 0; by < ph; by++) {
    for (let bx = 0; bx < pw; bx++) {
      let acc = 0;
      for (let dy = 0; dy < 4; dy++) {
        const rowStart = (by * 4 + dy) * w + bx * 4;
        acc += gray[rowStart] + gray[rowStart + 1] + gray[rowStart + 2] + gray[rowStart + 3];
      }
      pooled[by * pw + bx] = acc >> 4;
    }
  }

  const detail = cells ? edges / cells : 0;

  // Scaled by how much texture the frame has, so a busy nutrition table and
  // a sparse ingredients strip read the same for the same amount of drift.
  let motion = 0;
  if (prevGray && prevGray.length === pooled.length) {
    let diff = 0;
    for (let i = 0; i < pooled.length; i++) diff += Math.abs(pooled[i] - prevGray[i]);
    motion = diff / pooled.length / Math.max(detail, 0.05);
  }

  return {
    brightness: sum / n,
    glare: blown / n,
    detail,
    motion,
    gray: pooled // handed back in as `prevGray` on the next call
  };
}

// ============================================================
// PREVIEW — "Use this photo / Retake" before anything is analysed
// ============================================================

function wirePreview() {
  byId("useBtn").addEventListener("click", startAnalysis);
  byId("retakeBtn").addEventListener("click", retake);
}

function showPreview(file, source) {
  clearPending();
  pending = { file, source, thumb: "", url: URL.createObjectURL(file) };

  hideScanStatus();
  stopCameraStream();
  byId("scanHero").hidden = true;
  byId("inputTabs").hidden = true;
  byId("inputPanels").hidden = true;

  const box = byId("previewBox");
  const frame = byId("previewFrame");
  const img = byId("previewImg");
  const quality = byId("previewQuality");

  // reset from any earlier preview
  frame.classList.remove("analysing");
  const oldFallback = frame.querySelector(".preview-fallback");
  if (oldFallback) oldFallback.remove();
  img.hidden = false;
  quality.hidden = true;
  box.classList.remove("analysing");
  byId("previewActions").hidden = false;
  byId("useBtn").textContent = "Use this photo";
  byId("retakeBtn").textContent = source === "camera" ? "Retake" : "Choose another";
  box.hidden = false;

  img.onload = () => inspectPreview(img);
  img.onerror = () => {
    // e.g. an iPhone HEIC in a browser that can't draw it — still uploadable
    img.hidden = true;
    const note = document.createElement("div");
    note.className = "preview-fallback";
    note.textContent = `${file.name || "This photo"} can't be previewed here, but it can still be analysed.`;
    frame.appendChild(note);
  };
  img.src = pending.url;

  byId("useBtn").focus({ preventScroll: true });
  box.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "start" });
}

function inspectPreview(img) {
  const longest = Math.max(img.naturalWidth, img.naturalHeight) || 1;
  const scale = Math.min(1, 480 / longest);
  const w = Math.max(2, Math.round(img.naturalWidth * scale));
  const h = Math.max(2, Math.round(img.naturalHeight * scale));

  const c = document.createElement("canvas");
  c.width = w;
  c.height = h;
  const ctx = c.getContext("2d", { willReadFrequently: true });
  ctx.drawImage(img, 0, 0, w, h);

  try {
    showPreviewQuality(analyseFrame(ctx.getImageData(0, 0, w, h), null));
  } catch (e) {
    /* pixel access blocked — skip the advisory, the photo is still usable */
  }

  pending.thumb = makeThumb(img);
}

function showPreviewQuality(m) {
  const el = byId("previewQuality");
  let ok = false;
  let text;

  if (m.brightness < DARK_BELOW) {
    text = "This looks quite dark, so small print may be missed. A brighter retake usually reads better.";
  } else if (m.glare > GLARE_ABOVE) {
    text = "There's a lot of glare on this one. Tilting the pack a little often clears it.";
  } else if (m.detail < DETAIL_MIN) {
    text = "This may be blurry or taken from too far away. If the small print looks fuzzy, retake it closer.";
  } else {
    ok = true;
    text = "Looks sharp and well lit.";
  }

  el.classList.toggle("warn", !ok);
  el.innerHTML = `<span aria-hidden="true">${ok ? "✓" : "⚠️"}</span><span>${escapeHtml(text)}</span>`;
  el.hidden = false;
}

// A small square crop for the history list (a few KB, stored with the scan).
function makeThumb(img) {
  try {
    const size = 96;
    const c = document.createElement("canvas");
    c.width = size;
    c.height = size;
    const ctx = c.getContext("2d");
    const side = Math.min(img.naturalWidth, img.naturalHeight);
    const sx = (img.naturalWidth - side) / 2;
    const sy = (img.naturalHeight - side) / 2;
    ctx.drawImage(img, sx, sy, side, side, 0, 0, size, size);
    return c.toDataURL("image/jpeg", 0.6);
  } catch (e) {
    return "";
  }
}

function clearPending() {
  if (pending.url) URL.revokeObjectURL(pending.url);
  pending = { file: null, source: null, thumb: "", url: null };
}

function showInputs() {
  byId("previewBox").hidden = true;
  byId("scanHero").hidden = false;
  byId("inputTabs").hidden = false;
  byId("inputPanels").hidden = false;
}

function retake() {
  const source = pending.source;
  cancelActiveRequest();
  hideScanStatus();
  clearPending();
  showInputs();

  if (source === "camera") {
    selectInputTab("scan");
    startCamera();
  } else {
    selectInputTab("upload");
    byId("imageInput").click(); // still inside the click that got us here
  }
}

function resetScanUI() {
  cancelActiveRequest();
  hideScanStatus();
  clearPending();
  showInputs();
}

// ============================================================
// LIVE CAMERA CAPTURE
// ============================================================
//
// The "Scan with camera" tab has exactly one path: getUserMedia opens a
// live camera stream right in the page. There is no upload fallback here
// on purpose — that's what the "Upload photo" tab is for. If the browser
// can't give us a camera stream (no support, permission denied, no camera
// device), we say so and let the person try again, rather than quietly
// handing them a file picker.

let cameraStream = null;
let cameraFacing = "environment";
let torchOn = false;

const guidance = { timer: null, canvas: null, ctx: null, prev: null, motion: 0, candidate: "", streak: 0, shown: "" };

function wireCamera() {
  byId("cameraOpenBtn").addEventListener("click", () => startCamera());
  byId("cameraCancelBtn").addEventListener("click", () => stopCameraStream());
  byId("cameraShutterBtn").addEventListener("click", () => capturePhoto());
  byId("cameraTorchBtn").addEventListener("click", () => toggleTorch());
  byId("cameraSwitchBtn").addEventListener("click", () => {
    cameraFacing = cameraFacing === "environment" ? "user" : "environment";
    startCamera();
  });
}

async function startCamera() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    showScanError("This browser can't open a live camera. Try a different browser, or use \"Upload photo\" instead.");
    return;
  }

  stopCameraStream(false);
  hideScanStatus();

  try {
    cameraStream = await navigator.mediaDevices.getUserMedia({
      video: { facingMode: { ideal: cameraFacing } },
      audio: false
    });
  } catch (err) {
    showScanError(cameraPermissionMessage(err));
    return;
  }

  const video = byId("cameraVideo");
  video.srcObject = cameraStream;

  byId("cameraOpenBtn").hidden = true;
  byId("cameraBox").hidden = false;

  setHint("start");
  setupTorch();
  startGuidance();
  maybeShowSwitchButton();
}

async function maybeShowSwitchButton() {
  const switchBtn = byId("cameraSwitchBtn");
  try {
    const devices = await navigator.mediaDevices.enumerateDevices();
    const cameras = devices.filter((d) => d.kind === "videoinput");
    switchBtn.hidden = cameras.length < 2;
  } catch {
    switchBtn.hidden = true;
  }
}

function capturePhoto() {
  const video = byId("cameraVideo");
  const canvas = byId("cameraCanvas");

  if (!REDUCED_MOTION) {
    const frame = byId("cameraFrame");
    frame.classList.remove("flash");
    void frame.offsetWidth; // restart the animation if it's mid-flash already
    frame.classList.add("flash");
  }

  canvas.width = video.videoWidth;
  canvas.height = video.videoHeight;
  canvas.getContext("2d").drawImage(video, 0, 0, canvas.width, canvas.height);

  canvas.toBlob(
    (blob) => {
      if (!blob) {
        showScanError("Couldn't capture that photo — try again.");
        return;
      }
      const file = new File([blob], `scan-${Date.now()}.jpg`, { type: "image/jpeg" });
      stopCameraStream();
      handleChosenFile(file, "camera"); // preview first — nothing is sent yet
    },
    "image/jpeg",
    0.92
  );
}

function stopCameraStream(resetUI = true) {
  stopGuidance();

  if (cameraStream) {
    cameraStream.getTracks().forEach((track) => track.stop());
    cameraStream = null;
  }
  torchOn = false;

  if (resetUI) {
    byId("cameraBox").hidden = true;
    byId("cameraOpenBtn").hidden = false;
  }
}

function cameraPermissionMessage(err) {
  if (err && err.name === "NotAllowedError") {
    return "Camera access was blocked — allow camera permission for this site in your browser settings, then try again.";
  }
  if (err && err.name === "NotFoundError") {
    return "No camera was found on this device.";
  }
  return "Couldn't open the camera. If you're viewing this inside an embedded preview (like an editor's built-in browser), open the page in a full browser tab instead.";
}

// ---- torch (only offered where the browser actually supports it) ----

function setupTorch() {
  const btn = byId("cameraTorchBtn");
  btn.hidden = true;
  btn.setAttribute("aria-pressed", "false");
  btn.setAttribute("aria-label", "Turn torch on");

  const track = cameraStream && cameraStream.getVideoTracks()[0];
  if (!track || !track.getCapabilities) return;

  try {
    if (track.getCapabilities().torch) btn.hidden = false;
  } catch (e) {
    /* capabilities unavailable */
  }
}

async function toggleTorch() {
  const track = cameraStream && cameraStream.getVideoTracks()[0];
  const btn = byId("cameraTorchBtn");
  if (!track) return;

  const next = !torchOn;
  try {
    await track.applyConstraints({ advanced: [{ torch: next }] });
    torchOn = next;
    btn.setAttribute("aria-pressed", String(torchOn));
    btn.setAttribute("aria-label", torchOn ? "Turn torch off" : "Turn torch on");
  } catch (e) {
    btn.hidden = true; // this camera turned out not to support it
  }
}

// ---- live framing guidance ----

function startGuidance() {
  stopGuidance();
  guidance.canvas = document.createElement("canvas");
  guidance.ctx = guidance.canvas.getContext("2d", { willReadFrequently: true });
  guidance.prev = null;
  guidance.motion = 0;
  guidance.candidate = "";
  guidance.streak = 0;
  guidance.shown = "start";
  guidance.timer = setInterval(guidanceTick, 350);
}

function stopGuidance() {
  clearInterval(guidance.timer);
  guidance.timer = null;
}

function guidanceTick() {
  const video = byId("cameraVideo");
  if (!video || video.readyState < 2 || !video.videoWidth) return;

  const w = 320;
  const h = Math.max(2, Math.round((w * video.videoHeight) / video.videoWidth));
  guidance.canvas.width = w;
  guidance.canvas.height = h;

  let m;
  try {
    guidance.ctx.drawImage(video, 0, 0, w, h);
    m = analyseFrame(guidance.ctx.getImageData(0, 0, w, h), guidance.prev);
  } catch (e) {
    return;
  }
  guidance.prev = m.gray;
  guidance.motion = guidance.motion * 0.5 + m.motion * 0.5; // smooth out single jolts

  let state = "ok";
  if (m.brightness < DARK_BELOW) state = "dark";
  else if (m.glare > GLARE_ABOVE) state = "glare";
  else if (guidance.motion > MOTION_ABOVE) state = "moving";
  else if (m.detail < DETAIL_MIN) state = "far";

  // Only switch the hint after it's been stable for a moment, and be a bit
  // stickier about leaving "looks good" — otherwise the text flickers.
  if (state === guidance.candidate) guidance.streak++;
  else {
    guidance.candidate = state;
    guidance.streak = 1;
  }
  const needed = guidance.shown === "ok" ? 3 : 2;
  if (guidance.streak >= needed && guidance.shown !== state) setHint(state);
}

function setHint(state) {
  guidance.shown = state;
  const torchAvailable = !byId("cameraTorchBtn").hidden;
  const texts = {
    start: "Line the label up inside the frame",
    dark: torchAvailable ? "Too dark — add light or use the torch" : "Too dark — move somewhere brighter",
    glare: "Too much glare — tilt the pack a little",
    moving: "Hold steady",
    far: "Move closer — fill the frame with the label",
    ok: "Looks good — tap the shutter"
  };
  byId("cameraHintText").textContent = texts[state] || texts.start;
  byId("cameraHint").classList.toggle("ok", state === "ok");
  byId("cameraFrame").classList.toggle("is-ready", state === "ok");
}

// ============================================================
// ANALYSIS CALL — real upload progress, honest waiting
// ============================================================
//
// The server does OCR, clean-up, explanation and scoring in one request and
// answers once, so from the browser only three things are actually
// observable: the upload, the wait for the answer, and receiving it. Those
// are the three stages shown — no invented sub-steps on a timer. (Showing
// OCR / explain / score separately would need the server to report them.)

function startAnalysis() {
  if (!pending.file || activeRequest) return;

  byId("previewActions").hidden = true;
  byId("previewFrame").classList.add("analysing");
  byId("previewBox").classList.add("analysing");

  const formData = new FormData();
  formData.append("image", pending.file);

  const xhr = new XMLHttpRequest();
  activeRequest = { xhr, elapsedTimer: null, startedAt: Date.now() };
  showScanLoading(); // after the request exists, so it can start the elapsed timer

  xhr.open("POST", "/api/analyze-image");

  xhr.upload.onprogress = (e) => {
    if (e.lengthComputable) setUploadProgress(e.loaded / e.total);
  };
  xhr.upload.onload = () => {
    setUploadProgress(1);
    setStage("analyse");
  };

  xhr.onload = () => {
    let data = null;
    try {
      data = JSON.parse(xhr.responseText);
    } catch (e) {
      /* not JSON — handled below */
    }

    if (xhr.status === 413) {
      failScan("That photo is too large to upload. Try a smaller one, or retake it.");
    } else if (data && data.success) {
      setStage("finish");
      finishScan(data);
    } else if (data && data.message) {
      failScan(data.message);
    } else {
      failScan("The server hit a problem reading that image. Try again in a moment.");
    }
  };

  xhr.onerror = () => failScan("Couldn't reach FoodLens. Check your connection and try again.");

  xhr.send(formData);
}

function cancelActiveRequest() {
  if (!activeRequest) return;
  clearInterval(activeRequest.elapsedTimer);
  activeRequest.xhr.onload = activeRequest.xhr.onerror = null;
  activeRequest.xhr.abort();
  activeRequest = null;
}

async function finishScan(data) {
  if (activeRequest) clearInterval(activeRequest.elapsedTimer);
  activeRequest = null;

  // Grab whatever was the most recent scored scan *before* this one lands,
  // so results.html can show a "vs your last scan" delta. Best-effort —
  // if it fails, the delta chip simply won't show.
  let prevScore = null;
  try {
    const prior = await getHistory();
    const priorScored = prior.find(entryIsScored);
    if (priorScored) prevScore = priorScored.score;
  } catch (e) {
    /* ignore */
  }
  setPrevScoreForCompare(prevScore);

  // Wait for the save to reach the database before leaving this page —
  // navigating away can cancel an in-flight request otherwise.
  await saveHistoryEntry(data, pending.thumb, pending.source);
  setLatestResult(data);
  window.location.href = "results.html";
}

function failScan(message) {
  if (activeRequest) clearInterval(activeRequest.elapsedTimer);
  activeRequest = null;

  showScanError(message);

  // Leave the photo on screen with its buttons so the person can simply
  // try again, or retake.
  if (pending.file) {
    byId("previewFrame").classList.remove("analysing");
    byId("previewBox").classList.remove("analysing");
    byId("previewActions").hidden = false;
    byId("useBtn").textContent = "Try again";
  }
}

// ============================================================
// LOADING / ERROR
// ============================================================

function announce(message) {
  const live = byId("scanLive");
  live.textContent = "";
  setTimeout(() => (live.textContent = message), 60);
}

function showScanLoading() {
  const el = scanStatus();
  el.hidden = false;
  el.innerHTML = `
    <div class="loading">
      <div class="scan-line"></div>
      <div class="loading-basket" aria-hidden="true"><span>🍞</span><span>🥛</span><span>🍫</span><span>🧂</span></div>
      <p class="loading-stage" aria-hidden="true"><span id="stageText"></span><span class="elapsed" id="elapsed"></span></p>
      <div class="upload-bar" id="uploadBar" aria-hidden="true"><span></span></div>
      <div class="step-track" aria-hidden="true">
        ${SCAN_STAGES.map(
          (s) => `<span class="step-chip" data-step="${s.key}"><span class="dot"></span>${s.label}${
            s.key === "upload" ? ` <span class="pct" id="uploadPct"></span>` : ""
          }</span>`
        ).join("")}
      </div>
      <div class="skel-hero" aria-hidden="true">
        <span class="skel skel-circle"></span>
        <div>
          <span class="skel skel-line lg w60"></span>
          <span class="skel skel-line w90"></span>
          <span class="skel skel-line w40"></span>
        </div>
      </div>
      <button class="btn-ghost loading-cancel" id="cancelScanBtn" type="button">Cancel</button>
    </div>
  `;

  el.scrollIntoView({ behavior: REDUCED_MOTION ? "auto" : "smooth", block: "nearest" });

  byId("cancelScanBtn").addEventListener("click", () => {
    cancelActiveRequest();
    hideScanStatus();
    byId("previewFrame").classList.remove("analysing");
    byId("previewBox").classList.remove("analysing");
    byId("previewActions").hidden = false;
    byId("useBtn").focus({ preventScroll: true });
  });

  setStage("upload");

  // Elapsed seconds are real, and only shown while we're waiting on the server.
  const req = activeRequest;
  if (req) {
    req.elapsedTimer = setInterval(() => {
      const secs = Math.round((Date.now() - req.startedAt) / 1000);
      const stage = scanStatus().querySelector(".step-chip.active");
      const elapsed = byId("elapsed");
      if (!elapsed || !stage || stage.dataset.step !== "analyse") return;
      elapsed.textContent = `${secs} s`;
      if (secs >= 25) byId("stageText").textContent = "Still working — busy labels can take a little longer";
    }, 1000);
  }
}

function setUploadProgress(fraction) {
  const bar = byId("uploadBar");
  const pct = byId("uploadPct");
  if (!bar) return;
  const p = Math.max(0, Math.min(1, fraction));
  bar.firstElementChild.style.width = `${Math.round(p * 100)}%`;
  if (pct) pct.textContent = p < 1 ? `${Math.round(p * 100)}%` : "";
}

function setStage(key) {
  const el = scanStatus();
  const idx = SCAN_STAGES.findIndex((s) => s.key === key);

  el.querySelectorAll(".step-chip").forEach((chip, i) => {
    chip.classList.toggle("active", i === idx);
    chip.classList.toggle("done", i < idx);
  });

  const text = {
    upload: "Uploading your photo",
    analyse: "Reading the label and working out what it means",
    finish: "Preparing your result"
  }[key];

  const stageText = byId("stageText");
  const bar = byId("uploadBar");
  if (stageText) stageText.textContent = text;
  if (bar) {
    bar.classList.toggle("indeterminate", key === "analyse");
    if (key === "analyse") bar.firstElementChild.style.width = ""; // let the sweep animation size it
  }
  if (bar && key === "finish") bar.hidden = true;

  const elapsed = byId("elapsed");
  if (elapsed && key !== "analyse") elapsed.textContent = "";

  announce(text);
}

function hideScanStatus() {
  const el = scanStatus();
  el.hidden = true;
  el.innerHTML = "";
}

function showScanError(message) {
  const el = scanStatus();
  el.hidden = false;
  el.innerHTML = `
    <div class="error-box">
      <strong>That label didn't come through</strong>
      <p style="margin:0">${escapeHtml(message)}</p>
    </div>
  `;

  // the persistent alert region is what screen readers announce
  const alertEl = byId("scanAlert");
  alertEl.textContent = "";
  setTimeout(() => (alertEl.textContent = `That label didn't come through. ${message}`), 60);
}

window.addEventListener("beforeunload", () => stopCameraStream(false));