let currentUser = null;
let allEntries = [];
const filterState = { q: "", band: "all" };

const BAND_LABEL = { good: "Good", okay: "Okay", poor: "Poor" };

document.addEventListener("DOMContentLoaded", async () => {
  currentUser = requireAuth();
  if (!currentUser) return; // redirected to login

  plantProduce();
  wireLogout();
  wireBrandHome();
  wireDashNav();

  document.getElementById("userPill").textContent = currentUser.name || currentUser.email;
  const firstName = (currentUser.name || currentUser.email || "").split(" ")[0].split("@")[0];
  document.getElementById("dashGreeting").textContent = firstName
    ? `Welcome back, ${firstName} 🥗`
    : "Welcome back 🥗";

  await renderDashboard();
});

function wireDashNav() {
  document.getElementById("startScanBtn").addEventListener("click", () => {
    window.location.href = "scan.html";
  });
}

async function renderDashboard() {
  allEntries = await getHistory();
  const entries = allEntries;
  const totalScans = entries.length;

  // A label the backend couldn't score is saved with a placeholder 0 — it
  // shouldn't drag the average down or show up as a "0 out of 100" point.
  const scored = entries.filter(entryIsScored);
  const avgScore = scored.length ? Math.round(scored.reduce((s, e) => s + (e.score || 0), 0) / scored.length) : 0;
  const totalFlags = entries.reduce((s, e) => s + (e.flagsCount || 0), 0);

  countUp(document.getElementById("statScans"), totalScans);
  countUp(document.getElementById("statAvg"), avgScore);
  countUp(document.getElementById("statFlags"), totalFlags);
  renderSparkline(scored);

  const streak = computeStreak(entries);
  countUp(document.getElementById("statStreak"), streak);
  paintStreakCard(streak);

  const tools = document.getElementById("historyTools");
  const groups = document.getElementById("historyGroups");

  if (!entries.length) {
    tools.hidden = true;
    groups.innerHTML = `<div class="history-empty">Nothing scanned yet. Start with the back of whatever is closest to you.</div>`;
    return;
  }

  tools.hidden = false;
  wireHistoryTools();
  paintHistory({ animate: true });
}

// ============================================================
// SCORE TREND SPARKLINE
// ============================================================

function renderSparkline(scored) {
  const host = document.getElementById("statSpark");
  if (!host) return;

  // history is stored newest-first; the line should read oldest → newest
  const pts = scored.slice(0, 12).map((e) => e.score).reverse();

  if (pts.length < 2) {
    host.innerHTML = `<span class="spark-hint">Scan two labels to see a trend</span>`;
    return;
  }

  const W = 92;
  const H = 34;
  const pad = 4;
  const min = Math.min(...pts);
  const max = Math.max(...pts);
  // keep at least a 20-point window so tiny wobbles don't look dramatic
  const span = Math.max(20, max - min + 10);
  const lo = (min + max) / 2 - span / 2;

  const xy = pts.map((v, i) => [
    pad + (i * (W - pad * 2)) / (pts.length - 1),
    H - pad - ((v - lo) / span) * (H - pad * 2)
  ]);

  const line = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)} ${y.toFixed(1)}`).join(" ");
  const area = `${line} L${xy[xy.length - 1][0].toFixed(1)} ${H} L${xy[0][0].toFixed(1)} ${H} Z`;

  let len = 0;
  for (let i = 1; i < xy.length; i++) len += Math.hypot(xy[i][0] - xy[i - 1][0], xy[i][1] - xy[i - 1][1]);

  const last = pts[pts.length - 1];
  const earlier = pts.slice(0, -1);
  const diff = Math.round(last - earlier.reduce((a, b) => a + b, 0) / earlier.length);
  const trendClass = diff >= 2 ? "up" : diff <= -2 ? "down" : "flat";
  const trendText = diff >= 2 ? `Up ${diff}` : diff <= -2 ? `Down ${Math.abs(diff)}` : "Steady";
  const [lx, ly] = xy[xy.length - 1];

  host.innerHTML = `
    <svg class="spark" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" role="img"
         aria-label="Score trend over your last ${pts.length} scored scans. Latest ${last}, ${trendText.toLowerCase()} on your earlier average.">
      <path class="spark-area" d="${area}"></path>
      <path class="spark-line" d="${line}" style="--len:${Math.ceil(len) + 4}"></path>
      <circle class="spark-dot" cx="${lx.toFixed(1)}" cy="${ly.toFixed(1)}" r="4.5" fill="${scoreColor(last)}"></circle>
    </svg>
    <span class="spark-trend ${trendClass}" aria-hidden="true">${trendText}</span>`;
}

// ============================================================
// STREAK: consecutive calendar days with at least one scan
// ============================================================

// A streak is "alive" through the end of today even if today hasn't
// been scanned yet — it only breaks once a full day is skipped.
function computeStreak(entries) {
  const days = new Set(entries.map((e) => new Date(e.ts).toDateString()));
  if (!days.size) return 0;

  const cursor = new Date();
  cursor.setHours(0, 0, 0, 0);
  if (!days.has(cursor.toDateString())) cursor.setDate(cursor.getDate() - 1);

  let streak = 0;
  while (days.has(cursor.toDateString())) {
    streak++;
    cursor.setDate(cursor.getDate() - 1);
  }
  return streak;
}

function paintStreakCard(streak) {
  const icon = document.getElementById("streakIcon");
  if (icon) icon.classList.toggle("flame-lit", streak > 0);
}

// ============================================================
// HISTORY: search + score filter
// ============================================================

let historyToolsWired = false;

function wireHistoryTools() {
  buildFilterChips();
  if (historyToolsWired) return;
  historyToolsWired = true;

  document.getElementById("historySearch").addEventListener("input", (e) => {
    filterState.q = e.target.value;
    paintHistory({ animate: false });
  });

  document.getElementById("filterChips").addEventListener("click", (e) => {
    const chip = e.target.closest(".filter-chip");
    if (!chip) return;
    filterState.band = chip.dataset.band;
    buildFilterChips();
    paintHistory({ animate: false });
  });
}

function buildFilterChips() {
  const counts = { all: allEntries.length, good: 0, okay: 0, poor: 0 };
  allEntries.forEach((e) => {
    if (entryIsScored(e)) counts[scoreBand(e.score)] += 1;
  });

  const defs = [
    { band: "all", label: "All" },
    { band: "good", label: BAND_LABEL.good },
    { band: "okay", label: BAND_LABEL.okay },
    { band: "poor", label: BAND_LABEL.poor }
  ];

  document.getElementById("filterChips").innerHTML = defs
    .map(
      (d) => `
      <button type="button" class="filter-chip" data-band="${d.band}" aria-pressed="${filterState.band === d.band}">
        ${d.band === "all" ? "" : `<span class="fc-dot" aria-hidden="true"></span>`}
        ${d.label}
        <span class="fc-count">${counts[d.band]}</span>
      </button>`
    )
    .join("");
}

function entrySearchText(e) {
  const names = ((e.data || {}).ingredients || []).map((i) => i.simple_name).filter(Boolean);
  return [e.name, e.scoreLabel, ...names].join(" ").toLowerCase();
}

// Recent scans are split into two independently-rendered groups — one for
// photos picked from the file/upload flow, one for photos taken with the
// live camera — so the two capture methods don't get mixed into one list.
function historyRowHtml(e, i) {
  const isScored = entryIsScored(e);
  const color = isScored ? scoreColor(e.score) : "var(--line)";
  const scoreText = isScored ? e.score : "—";
  const date = new Date(e.ts);
  const dateStr = date.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  const thumb =
    typeof e.thumb === "string" && e.thumb.startsWith("data:image/")
      ? `<img src="${escapeHtml(e.thumb)}" alt="" loading="lazy">`
      : `<span aria-hidden="true">${entryEmoji(e)}</span>`;
  const aria = `${e.name}. ${isScored ? `${e.scoreLabel}, score ${e.score} out of 100` : "Not scored"}. ${dateStr}. Open scan.`;

  return `
    <div class="history-item">
    <button class="history-row" data-id="${e.id}" type="button" aria-label="${escapeHtml(aria)}" style="animation-delay:${i * 0.05}s">
      <span class="history-thumb">${thumb}</span>
      <span class="history-main">
        <span class="history-name">${escapeHtml(e.name)}</span>
        <span class="history-meta">${escapeHtml(e.scoreLabel)} · ${dateStr}${
    e.flagsCount ? ` · ${e.flagsCount} to look at` : ""
  }</span>
      </span>
      <span class="history-score" style="border-color:${color};color:${isScored ? color : "var(--ink-3)"}" aria-hidden="true">${scoreText}</span>
      <svg class="history-chev" width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
    </button>
    <button class="history-rename" type="button" data-rename="${e.id}" aria-label="Rename ${escapeHtml(e.name)}" title="Rename">
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M4 20h4L19 9a2.1 2.1 0 0 0-3-3L5 17l-1 3z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><path d="M14 8l3 3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>
    </button>
    </div>`;
}

function wireHistoryRowClicks(list) {
  list.querySelectorAll(".history-row").forEach((row) => {
    row.addEventListener("click", () => {
      const entry = allEntries.find((e) => e.id === row.dataset.id);
      if (entry) {
        setPrevScoreForCompare(null); // reopening an old scan — no "vs last scan" delta
        setLatestResult({ ...entry.data, product_guess: entry.name }); // carries any custom name
        window.location.href = "results.html";
      }
    });
  });
}

function paintHistoryGroup(source, rows, animate) {
  const group = document.getElementById(`historyGroup-${source}`);
  const list = document.getElementById(`historyList-${source}`);
  const count = document.getElementById(`historyCount-${source}`);
  if (!group || !list) return;

  group.hidden = !rows.length;
  if (!rows.length) return;

  list.classList.toggle("still", !animate);
  if (count) count.textContent = rows.length;
  list.innerHTML = rows.map((e, i) => historyRowHtml(e, i)).join("");
  wireHistoryRowClicks(list);
  wireRenameButtons(list);
}

function paintHistory({ animate }) {
  const status = document.getElementById("historyStatus");
  const q = filterState.q.trim().toLowerCase();

  const matches = allEntries.filter((e) => {
    const bandOk = filterState.band === "all" || (entryIsScored(e) && scoreBand(e.score) === filterState.band);
    return bandOk && (!q || entrySearchText(e).includes(q));
  });

  const uploadRows = matches.filter((e) => e.source !== "camera");
  const cameraRows = matches.filter((e) => e.source === "camera");

  if (!matches.length) {
    document.getElementById("historyGroup-upload").hidden = true;
    document.getElementById("historyGroup-camera").hidden = true;

    const groups = document.getElementById("historyGroups");
    let empty = document.getElementById("historyEmptyState");
    if (!empty) {
      empty = document.createElement("div");
      empty.id = "historyEmptyState";
      groups.appendChild(empty);
    }
    empty.innerHTML = `
      <div class="history-empty">
        No scans match${q ? ` “${escapeHtml(filterState.q.trim())}”` : ""}${filterState.band !== "all" ? ` in ${BAND_LABEL[filterState.band]}` : ""}.<br>
        <button type="button" class="link-btn" id="clearFilters">Clear filters</button>
      </div>`;
    document.getElementById("clearFilters").addEventListener("click", clearHistoryFilters);
    status.textContent = "No scans match.";
    return;
  }

  const oldEmpty = document.getElementById("historyEmptyState");
  if (oldEmpty) oldEmpty.remove();

  paintHistoryGroup("upload", uploadRows, animate);
  paintHistoryGroup("camera", cameraRows, animate);

  status.textContent =
    matches.length === allEntries.length ? "" : `${matches.length} of ${allEntries.length} scans shown.`;
}

function clearHistoryFilters() {
  filterState.q = "";
  filterState.band = "all";
  document.getElementById("historySearch").value = "";
  buildFilterChips();
  paintHistory({ animate: false });
}

// ============================================================
// RENAME A SCAN
// ============================================================

let renameTargetId = null;
let renameWired = false;

function wireRenameButtons(list) {
  wireRenameDialog();
  list.querySelectorAll("[data-rename]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const entry = allEntries.find((e) => e.id === btn.dataset.rename);
      if (entry) openRenameDialog(entry);
    });
  });
}

function openRenameDialog(entry) {
  renameTargetId = entry.id;
  const input = document.getElementById("renameInput");
  const err = document.getElementById("renameError");
  err.hidden = true;
  input.value = entry.name;
  document.getElementById("renameDialog").showModal();
  input.focus();
  input.select();
}

function wireRenameDialog() {
  if (renameWired) return;
  renameWired = true;

  const dialog = document.getElementById("renameDialog");
  const input = document.getElementById("renameInput");
  const saveBtn = document.getElementById("renameSave");
  const err = document.getElementById("renameError");

  const close = () => dialog.close();
  document.getElementById("renameClose").addEventListener("click", close);
  document.getElementById("renameCancel").addEventListener("click", close);
  // click on the dimmed backdrop closes it too
  dialog.addEventListener("click", (ev) => { if (ev.target === dialog) close(); });

  const save = async () => {
    const name = input.value.replace(/\s+/g, " ").trim().slice(0, 80);
    if (!name) {
      err.textContent = "Give it a name first.";
      err.hidden = false;
      return;
    }
    saveBtn.disabled = true;
    saveBtn.textContent = "Saving…";
    try {
      const res = await fetch(`/api/results/${renameTargetId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: currentUser.id, name })
      });
      const body = await res.json();
      if (!body.success) throw new Error(body.message || "Couldn't rename.");

      const entry = allEntries.find((e) => e.id === renameTargetId);
      if (entry) entry.name = body.name;
      dialog.close();
      paintHistory({ animate: false });
    } catch (e) {
      err.textContent = e.message || "Couldn't rename — try again.";
      err.hidden = false;
    } finally {
      saveBtn.disabled = false;
      saveBtn.textContent = "Save";
    }
  };

  saveBtn.addEventListener("click", save);
  input.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") { ev.preventDefault(); save(); }
  });
}