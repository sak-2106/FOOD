// ============================================================
// SHARED STATE / CONSTANTS
// ============================================================

const motionQuery = window.matchMedia("(prefers-reduced-motion: reduce)");
let REDUCED_MOTION = motionQuery.matches;

// If someone turns "reduce motion" on while the page is open, stop the
// drifting background straight away instead of waiting for a reload.
motionQuery.addEventListener("change", (e) => {
  REDUCED_MOTION = e.matches;
  if (e.matches) {
    const layer = document.getElementById("produceLayer");
    if (layer) layer.replaceChildren();
  }
});

// ============================================================
// BACKGROUND PRODUCE ANIMATION
// ============================================================

const PRODUCE = ["🥦", "🍅", "🥕", "🌽", "🫐", "🥑", "🍋", "🌾", "🥬", "🍇", "🥔", "🫒"];

function plantProduce() {
  const layer = document.getElementById("produceLayer");
  if (!layer || REDUCED_MOTION) return;

  PRODUCE.forEach((glyph, i) => {
    const span = document.createElement("span");
    span.textContent = glyph;
    span.style.left = `${(i * 8.3 + 4 + Math.random() * 5).toFixed(1)}%`;
    span.style.top = `${(Math.random() * 88 + 4).toFixed(1)}%`;
    span.style.setProperty("--size", `${22 + Math.random() * 26}px`);
    span.style.setProperty("--dur", `${18 + Math.random() * 16}s`);
    span.style.setProperty("--delay", `${(Math.random() * -20).toFixed(1)}s`);
    layer.appendChild(span);
  });
}

// ============================================================
// SESSION / AUTH
// ============================================================

function getCurrentUser() {
  try {
    const saved = sessionStorage.getItem("foodlens_user");
    return saved ? JSON.parse(saved) : null;
  } catch (e) {
    return null;
  }
}

// Call at the top of any page that requires a logged-in user.
// Redirects to login.html if nobody is signed in.
function requireAuth() {
  const user = getCurrentUser();
  if (!user) {
    window.location.href = "login.html";
    return null;
  }
  return user;
}

function logout() {
  sessionStorage.removeItem("foodlens_user");
  window.location.href = "login.html";
}

function wireLogout() {
  const btn = document.getElementById("logoutBtn");
  if (btn) btn.addEventListener("click", logout);
}

function wireBrandHome() {
  const btn = document.getElementById("brandHome");
  if (btn) btn.addEventListener("click", () => (window.location.href = "dashboard.html"));
}

// ============================================================
// HISTORY (per-user, stored server-side in the database)
// ============================================================

// Fetches this user's saved scans from the backend. Returns [] if nobody
// is signed in, or if the request fails for any reason.
async function getHistory() {
  const user = getCurrentUser();
  if (!user || !user.id) return [];

  try {
    const res = await fetch(`/api/results/${user.id}`);
    const body = await res.json();
    return body.success ? body.entries : [];
  } catch (e) {
    return [];
  }
}

// Saves a finished scan to the database against the signed-in user.
// `source` is "upload" or "camera" — however this particular photo reached
// FoodLens — so history can later be split by how it was captured.
async function saveHistoryEntry(data, thumb, source) {
  const user = getCurrentUser();
  if (!user || !user.id) return;

  const flagsCount = countFlags(data);
  const top = (data.ingredients || [])[0];
  const name = data.product_guess || (top ? `Label with ${top.simple_name}` : "Scanned label");

  try {
    await fetch("/api/results", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        user_id: user.id,
        name,
        score: typeof data.score === "number" ? data.score : null,
        score_label: data.score_label || "Not scored",
        flags_count: flagsCount,
        thumb: thumb || "",
        source: source === "camera" ? "camera" : "upload",
        data
      })
    });
  } catch (e) {
    /* couldn't reach the server — the scan result itself still shows on
       results.html, it just won't appear in history */
  }
}

function countFlags(data) {
  const ings = data.ingredients || [];
  const fromVerdict = ings.filter((i) => i.verdict === "look").length;
  if (fromVerdict) return fromVerdict;
  return (data.allergens || []).length + (data.additives || []).length + (data.sweeteners || []).length;
}

// ============================================================
// "LATEST RESULT" HANDOFF BETWEEN PAGES
// ============================================================
// scan.html and dashboard.html (history rows) both write here before
// navigating to results.html, which reads it on load.

function setLatestResult(data) {
  try {
    sessionStorage.setItem("foodlens_latest_result", JSON.stringify(data));
  } catch (e) {
    /* ignore */
  }
}

function getLatestResult() {
  try {
    const raw = sessionStorage.getItem("foodlens_latest_result");
    return raw ? JSON.parse(raw) : null;
  } catch (e) {
    return null;
  }
}

// ============================================================
// SMALL UTILS
// ============================================================

function escapeHtml(value) {
  if (value === null || value === undefined) return "";
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function formatValue(v) {
  if (typeof v !== "number") return "—";
  return Number.isInteger(v) ? v.toString() : v.toFixed(1);
}

function shorten(text, max) {
  const s = String(text);
  return s.length > max ? s.slice(0, max - 1).trimEnd() + "…" : s;
}

// ============================================================
// CELEBRATIONS: confetti burst + toast
// Shared by dashboard.js (streak milestones) and results.js
// (best-score-yet moments). Both no-op gracefully under
// prefers-reduced-motion.
// ============================================================

const CONFETTI_COLORS = ["#5fd98a", "#e8b66d", "#e58f82", "#9cc9c0", "#dccb9f", "#f4f1e8"];

// Bursts small coloured pieces outward from originEl's center (or page
// center if no element given). Pure DOM + CSS, self-cleaning.
function fireConfetti(originEl, count = 26) {
  if (REDUCED_MOTION) return;

  let x = window.innerWidth / 2;
  let y = window.innerHeight / 2;
  if (originEl && originEl.getBoundingClientRect) {
    const r = originEl.getBoundingClientRect();
    x = r.left + r.width / 2;
    y = r.top + r.height / 2;
  }

  let field = document.getElementById("confettiField");
  if (!field) {
    field = document.createElement("div");
    field.id = "confettiField";
    field.className = "confetti-field";
    field.setAttribute("aria-hidden", "true");
    document.body.appendChild(field);
  }

  for (let i = 0; i < count; i++) {
    const piece = document.createElement("span");
    piece.className = "confetti-piece";
    const angle = (Math.PI * 2 * i) / count + (Math.random() * 0.6 - 0.3);
    const dist = 70 + Math.random() * 110;
    piece.style.left = `${x}px`;
    piece.style.top = `${y}px`;
    piece.style.setProperty("--dx", `${Math.cos(angle) * dist}px`);
    piece.style.setProperty("--dy", `${Math.sin(angle) * dist - 40}px`);
    piece.style.setProperty("--rot", `${(Math.random() * 720 - 360).toFixed(0)}deg`);
    piece.style.setProperty("--dur", `${(0.8 + Math.random() * 0.6).toFixed(2)}s`);
    piece.style.background = CONFETTI_COLORS[i % CONFETTI_COLORS.length];
    if (Math.random() > 0.5) piece.style.borderRadius = "50%";
    field.appendChild(piece);
    piece.addEventListener("animationend", () => piece.remove());
  }
}

let toastStack = null;

// Shows a small celebratory card in the corner: { emoji, title, sub }.
function showToast({ emoji = "🎉", title, sub = "" } = {}) {
  if (!title) return;
  if (!toastStack) {
    toastStack = document.createElement("div");
    toastStack.className = "toast-stack";
    toastStack.setAttribute("aria-live", "polite");
    document.body.appendChild(toastStack);
  }

  const card = document.createElement("div");
  card.className = "toast" + (REDUCED_MOTION ? " no-anim" : "");
  card.innerHTML = `
    <span class="toast-emoji" aria-hidden="true">${emoji}</span>
    <span class="toast-copy">
      <span class="toast-title">${escapeHtml(title)}</span>
      ${sub ? `<span class="toast-sub">${escapeHtml(sub)}</span>` : ""}
    </span>
    <button class="toast-close" type="button" aria-label="Dismiss">×</button>
  `;
  toastStack.appendChild(card);

  const remove = () => {
    card.classList.add("leaving");
    setTimeout(() => card.remove(), REDUCED_MOTION ? 0 : 320);
  };
  card.querySelector(".toast-close").addEventListener("click", remove);
  const timer = setTimeout(remove, 5200);
  card.addEventListener("pointerenter", () => clearTimeout(timer));
}

// ============================================================
// "SCORE VS LAST SCAN" HANDOFF
// scan.js stashes the previous top score right before saving a new
// entry; results.js reads (and clears) it once, so reopening an old
// scan from history later never shows a stale delta.
// ============================================================

function setPrevScoreForCompare(score) {
  try {
    if (typeof score === "number") sessionStorage.setItem("foodlens_prev_score", String(score));
    else sessionStorage.removeItem("foodlens_prev_score");
  } catch (e) {
    /* ignore */
  }
}

function takePrevScoreForCompare() {
  try {
    const raw = sessionStorage.getItem("foodlens_prev_score");
    sessionStorage.removeItem("foodlens_prev_score");
    return raw === null ? null : Number(raw);
  } catch (e) {
    return null;
  }
}

function ordinal(n) {
  const s = ["th", "st", "nd", "rd"];
  const v = n % 100;
  return n + (s[(v - 20) % 10] || s[v] || s[0]);
}

// Good / Okay / Poor — the same 75 and 50 cut-offs the backend uses for
// its own "Good choice" / "Okay" / "Worth a closer look" labels.
function scoreBand(score) {
  if (score >= 75) return "good";
  if (score >= 50) return "okay";
  return "poor";
}

// Older history entries pre-date the explicit `scored` flag.
function entryIsScored(entry) {
  if (typeof entry.scored === "boolean") return entry.scored;
  return entry.scoreLabel !== "Not scored";
}

// Whole-number for big values, one decimal for small ones, no trailing ".0".
function formatAmount(v) {
  if (typeof v !== "number" || !isFinite(v)) return "—";
  if (v >= 100) return String(Math.round(v));
  return String(Math.round(v * 10) / 10);
}

const EMOJI_MAP = [
  [/(wheat|atta|flour|grain|maida|semolina|oat|barley|rye)/i, "🌾"],
  [/(sugar|syrup|fructose|dextrose|jaggery|honey)/i, "🍬"],
  [/(milk|dairy|whey|cheese|butter|cream|curd|paneer)/i, "🥛"],
  [/(oil|fat|ghee|palm|shortening)/i, "🫒"],
  [/(salt|sodium chloride)/i, "🧂"],
  [/(cocoa|chocolate)/i, "🍫"],
  [/(peanut|almond|cashew|nut|walnut)/i, "🥜"],
  [/(soy|soya|lecithin)/i, "🫘"],
  [/(rice|corn|maize|starch)/i, "🌽"],
  [/(egg)/i, "🥚"],
  [/(fruit|apple|mango|orange|berry|lemon)/i, "🍎"],
  [/(veg|tomato|onion|garlic|potato|carrot|spinach)/i, "🥕"],
  [/(spice|masala|pepper|chilli|chili|cumin|turmeric)/i, "🌶️"],
  [/(flavour|flavor|essence)/i, "👃"],
  [/(colour|color|tartrazine|carmoisine)/i, "🎨"],
  [/(preservative|benzoate|sorbate|antioxidant|tbhq|bht)/i, "🧪"],
  [/(acid|citric|regulator|raising|emulsif|stabilis|stabiliz|thicken)/i, "⚗️"],
  [/(vitamin|mineral|iron|calcium|zinc)/i, "💊"],
  [/(water|aqua)/i, "💧"],
  [/(yeast)/i, "🍞"],
  [/\b(ins|e)\s?\d{3}/i, "🧪"]
];

function emojiFor(ing) {
  const hay = `${ing.simple_name || ""} ${ing.category || ""} ${ing.original || ""}`;
  for (const [re, glyph] of EMOJI_MAP) if (re.test(hay)) return glyph;
  return "🥄";
}

// A stand-in "thumbnail" for scans saved without a photo: the emoji of the
// heaviest ingredient, which is usually what the product mostly is.
function entryEmoji(entry) {
  const top = ((entry.data || {}).ingredients || [])[0];
  if (top) return top.emoji || emojiFor(top);
  return "🥫";
}

function scoreColor(score) {
  if (score >= 75) return "var(--good)";
  if (score >= 50) return "var(--warn)";
  return "var(--bad)";
}

function countUp(el, target, duration = 750) {
  if (!el) return;
  const start = Number(el.dataset.count) || 0;
  if (REDUCED_MOTION) {
    el.textContent = target;
    el.dataset.count = target;
    return;
  }
  const startTime = performance.now();

  function tick(now) {
    const progress = Math.min(1, (now - startTime) / duration);
    const eased = 1 - Math.pow(1 - progress, 3);
    el.textContent = Math.round(start + (target - start) * eased);
    if (progress < 1) requestAnimationFrame(tick);
    else el.dataset.count = target;
  }
  requestAnimationFrame(tick);
}

// ============================================================
// MICRO-INTERACTIONS — cursor-tracked spotlight & click ripple
// Delegated on `document` rather than bound per-element, since the hero
// card (results.js) and history rows (dashboard.js) are both rendered
// well after DOMContentLoaded — element-level listeners would miss them.
// ============================================================

const SPOTLIGHT_SELECTOR = ".auth-card, .hero-card, .stat-card";
const RIPPLE_SELECTOR = ".btn-primary, .scan-cta";

document.addEventListener("pointermove", (e) => {
  if (REDUCED_MOTION) return;
  const el = e.target.closest(SPOTLIGHT_SELECTOR);
  if (!el) return;
  const r = el.getBoundingClientRect();
  el.style.setProperty("--mx", `${((e.clientX - r.left) / r.width) * 100}%`);
  el.style.setProperty("--my", `${((e.clientY - r.top) / r.height) * 100}%`);
});

document.addEventListener("pointerdown", (e) => {
  if (REDUCED_MOTION) return;
  const el = e.target.closest(RIPPLE_SELECTOR);
  if (!el || el.disabled) return;

  const r = el.getBoundingClientRect();
  const mx = `${((e.clientX - r.left) / r.width) * 100}%`;
  const my = `${((e.clientY - r.top) / r.height) * 100}%`;
  el.style.setProperty("--mx", mx);
  el.style.setProperty("--my", my);

  let layer = el.querySelector(":scope > .ripple-layer");
  if (!layer) {
    layer = document.createElement("span");
    layer.className = "ripple-layer";
    el.appendChild(layer);
  }
  const ripple = document.createElement("span");
  ripple.className = "ripple";
  ripple.style.left = `${e.clientX - r.left}px`;
  ripple.style.top = `${e.clientY - r.top}px`;
  layer.appendChild(ripple);
  ripple.addEventListener("animationend", () => ripple.remove());
});

// Theme is fixed to the dark sage look — no toggle, no light variant.