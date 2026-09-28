"""
enrich.py
=========

Turns messy OCR text into clean, plain-English ingredient cards.

Why this file exists
--------------------
The static ingredients_db.json can only describe the ~34 ingredients somebody
typed into it by hand. Real labels contain thousands of names, in dozens of
spellings, and Tesseract mangles a good share of them
("Srdaper Serve (306)" instead of "Sunflower Oil"). No hand-written table
can keep up with that.

So the flow is now:

    OCR text ──▶ AI pass (cleans typos + explains every ingredient) ──▶ cards
                     │
                     └─ if no API key / no network / API error
                        ──▶ static ingredients_db.json + rules  (offline mode)

The JSON file is kept as a safety net, not as the main brain. Results are
cached in SQLite so the same product never costs a second API call.

Configuration (environment variables)
-------------------------------------
    ANTHROPIC_API_KEY   your key. Without it the app runs in offline mode.
    FOODLENS_AI         "off" to force offline mode. Default "on".
    FOODLENS_MODEL      model name. Default "claude-haiku-4-5-20251001"
                        (fast + cheap; use a larger model for tougher labels).
"""

import hashlib
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request

from quality import clean_ingredient_name, is_garbled
import knowledge

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CACHE_DB = os.path.join(BASE_DIR, "enrich_cache.db")

API_URL = "https://api.anthropic.com/v1/messages"
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "").strip()
MODEL = os.environ.get("FOODLENS_MODEL", "claude-haiku-4-5-20251001")
AI_ENABLED = os.environ.get("FOODLENS_AI", "on").lower() != "off" and bool(API_KEY)
TIMEOUT = int(os.environ.get("FOODLENS_TIMEOUT", "45"))


# ============================================================
# CACHE
# ============================================================

def _init_cache():
    conn = sqlite3.connect(CACHE_DB)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS label_cache (
            key        TEXT PRIMARY KEY,
            payload    TEXT NOT NULL,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    conn.commit()
    conn.close()


_init_cache()


def _cache_key(text):
    return hashlib.sha256(text.strip().lower().encode("utf-8")).hexdigest()


def cache_get(key):
    try:
        conn = sqlite3.connect(CACHE_DB)
        row = conn.execute(
            "SELECT payload FROM label_cache WHERE key = ?", (key,)
        ).fetchone()
        conn.close()
        return json.loads(row[0]) if row else None
    except Exception:
        return None


def cache_put(key, payload):
    try:
        conn = sqlite3.connect(CACHE_DB)
        conn.execute(
            "INSERT OR REPLACE INTO label_cache (key, payload) VALUES (?, ?)",
            (key, json.dumps(payload)),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# ============================================================
# PROMPT
# ============================================================

SYSTEM_PROMPT = """You read food packaging labels that have been scanned with OCR and turn them into short, plain explanations for ordinary shoppers.

The OCR text is noisy. Letters get swapped, numbers get inserted, words get merged, and commas sometimes print as periods so two unrelated things run together. Use food-labelling knowledge to recover what the label most likely said. "Srdaper Serve" near an oil list is probably "Sunflower Oil". "Ins 3076" is probably "INS 307b".

Two specific failure modes to guard against, because they are common in this OCR:

- Nutrition-table bleed: ingredients and the nutrition panel sit next to each other on the pack, and OCR often reads across the boundary. If a fragment mixes an ingredient with nutrition-table words ("Total Fat", "Energy", "Saturated Fatty Acids", "Kcal", "Sodium", a "%" tied to a nutrient rather than an ingredient percentage), that fragment is not a real ingredient — either recover the real ingredient name from the part before the nutrition text, or drop it.
- Unrecoverable noise: short runs of stray symbols and mixed-case nonsense ("V'Ta*'", "<> --", single stray letters strung together) are OCR debris, not an ingredient. Drop these entirely rather than inventing an ingredient to match them, and never output an ingredient name containing symbols like < > = { } | ~ ` or a string of digits with no clear unit.

If you cannot recover a real ingredient from a fragment, leave it out of the list — a shorter, accurate list beats a longer list with junk in it.

Write for someone standing in a shop aisle, not for a nutritionist:
- one_liner: at most 9 words, no jargon. "Plain sugar." "Keeps the oil from going rancid."
- what: 1–2 full sentences, max 40 words. Say what the ingredient actually is — its source or how it's made — not a vague restatement of its category. "A thickener made from fermented sugar" beats "A food additive used in this product." If you can name the specific compound or origin (a spice, a named oil, a named gum, a vitamin), do so, even from a garbled or partial OCR name you've recovered.
- why: one sentence on why a manufacturer adds it, max 15 words. Be concrete about its function (thickens, sweetens, preserves, colours, emulsifies) rather than "used in food production."
- technical: a second, deeper layer for readers who want the chemistry. This is NOT a rewrite of "what" in longer words. Fill in: the specific compound or compound class (chemical), the INS/E number if the label states one (ins), the functional class it belongs to (family), what it is actually extracted from or synthesised from (source), and 2-3 sentences of real mechanism (detail) — how it works, what it does to the food at a molecular level, what distinguishes it from similar ingredients. Use proper technical vocabulary here; this layer is allowed to assume a reader who wants it. Leave a field as an empty string rather than guessing. Put regulatory status, allergen derivation or intake caveats in notes.
- watch: ONLY when there is a real reason to pay attention (common allergen, added sugar, high-sodium ingredient, artificial colour, trans-fat risk, additive people commonly avoid). One sentence, max 20 words, calm and factual. Otherwise use an empty string.

Never leave "what" as a generic placeholder like "Listed on the label" or "An ingredient used in this product" — if the OCR name is too damaged to identify confidently, say what it most likely is given its context (position in the list, nearby ingredients, category conventions) rather than writing nothing useful.

verdict is "look" when watch is non-empty, otherwise "ok". Most ordinary ingredients (flour, water, oats, salt in small amounts, spices) are "ok". Do not mark everything "look" — a label where everything is flagged is useless.

Never diagnose, never prescribe, never say a food is dangerous. Describe, don't scare.

Reply with JSON only. No markdown fences, no commentary."""


USER_TEMPLATE = """Here is the OCR text from a food label:

<ocr>
{ocr}
</ocr>

Return this exact JSON shape:

{{
  "product_guess": "short product description or empty string",
  "ingredients": [
    {{
      "original": "cleaned-up name as printed on the label",
      "simple_name": "short everyday name, title case",
      "emoji": "one food emoji",
      "category": "Grain | Sweetener | Oil or fat | Dairy | Allergen | Preservative | Colour | Flavouring | Emulsifier | Additive | Spice | Vegetable | Fruit | Protein | Other",
      "percent": "percentage if the label states one, else empty string",
      "one_liner": "max 9 words",
      "what": "one sentence",
      "why": "one sentence",
      "watch": "one sentence or empty string",
      "verdict": "ok or look",
      "technical": {{
        "chemical": "the specific compound or compound class, or empty string",
        "ins": "INS or E number as printed, e.g. INS 322(i), or empty string",
        "family": "functional class, e.g. Emulsifier (phospholipid), or empty string",
        "source": "what it is derived from or synthesised from, or empty string",
        "detail": "2-3 sentences of real mechanism, or empty string",
        "notes": "regulatory status, allergen derivation or intake caveat, or empty string"
      }}
    }}
  ],
  "nutrition": {{
    "energy": {{"value": 0, "unit": "kcal"}},
    "fat": {{"value": 0, "unit": "g"}},
    "saturated_fat": {{"value": 0, "unit": "g"}},
    "trans_fat": {{"value": 0, "unit": "g"}},
    "carbohydrates": {{"value": 0, "unit": "g"}},
    "total_sugars": {{"value": 0, "unit": "g"}},
    "added_sugars": {{"value": 0, "unit": "g"}},
    "fiber": {{"value": 0, "unit": "g"}},
    "protein": {{"value": 0, "unit": "g"}},
    "sodium": {{"value": 0, "unit": "mg"}},
    "salt": {{"value": 0, "unit": "g"}}
  }}
}}

Keep the ingredients in the order they appear on the label. Include only nutrition keys the label actually states — omit the rest. If no ingredient list is readable, return an empty ingredients array."""


# ============================================================
# API CALL
# ============================================================

def _call_api(ocr_text):
    body = json.dumps(
        {
            "model": MODEL,
            "max_tokens": 6000,
            "system": SYSTEM_PROMPT,
            "messages": [
                {"role": "user", "content": USER_TEMPLATE.format(ocr=ocr_text[:12000])}
            ],
        }
    ).encode("utf-8")

    request = urllib.request.Request(
        API_URL,
        data=body,
        headers={
            "content-type": "application/json",
            "x-api-key": API_KEY,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
        payload = json.loads(response.read().decode("utf-8"))

    text = "".join(
        block.get("text", "")
        for block in payload.get("content", [])
        if block.get("type") == "text"
    )

    return _parse_json(text)


def _parse_json(text):
    """Models occasionally wrap JSON in prose or fences. Dig it out."""

    text = text.strip()
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    start = text.find("{")
    end = text.rfind("}")

    if start != -1 and end > start:
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass

    raise ValueError("Model did not return usable JSON.")


# ============================================================
# VALIDATION
# ============================================================

ALLOWED_VERDICTS = {"ok", "look"}


def _clean_item(item, position):
    """Never trust model output straight into the UI — same cleanup the offline path uses."""

    def text(key, limit, default=""):
        value = item.get(key, default)
        if not isinstance(value, str):
            value = str(value) if value is not None else default
        value = re.sub(r"\s+", " ", value).strip()
        return value[:limit]

    raw_simple = text("simple_name", 90) or text("original", 90)
    simple_name = clean_ingredient_name(raw_simple, max_len=60)

    if not simple_name:
        # The model handed back noise despite the prompt — drop it rather
        # than show a mystery ingredient. Caller filters out this sentinel.
        return None

    watch = text("watch", 220)
    verdict = item.get("verdict")

    if verdict not in ALLOWED_VERDICTS:
        verdict = "look" if watch else "ok"
    if verdict == "look" and not watch:
        watch = "Worth reading the detail on this one."

    emoji = text("emoji", 8)
    original = clean_ingredient_name(text("original", 200), max_len=90) or simple_name
    category = text("category", 40) or "Other"
    what = text("what", 320)
    why = text("why", 200)

    # The prompt asks for a real explanation, but if the model still comes
    # back thin (short, or a generic-sounding placeholder), fall back to
    # the same pattern rules the offline path uses rather than show nothing.
    if len(what) < 15:
        rule = knowledge.lookup(simple_name.lower(), original.lower())
        if rule:
            rule_category, rule_what, rule_why, rule_watch = rule
            what = rule_what
            why = why or rule_why
            if not watch and rule_watch:
                watch = rule_watch
                verdict = "look"

    # The technical block is optional — technical.py fills in whatever the
    # model left blank, so a thin answer here costs nothing.
    raw_technical = item.get("technical")
    raw_technical = raw_technical if isinstance(raw_technical, dict) else {}

    def tech(key, limit):
        value = raw_technical.get(key, "")
        if not isinstance(value, str):
            value = str(value) if value is not None else ""
        return re.sub(r"\s+", " ", value).strip()[:limit]

    technical_block = {
        "chemical": tech("chemical", 180),
        "ins": tech("ins", 24),
        "family": tech("family", 60),
        "source": tech("source", 140),
        "detail": tech("detail", 520),
        "notes": tech("notes", 260),
    }

    return {
        "original": original,
        "simple_name": simple_name,
        "emoji": emoji,
        "category": category,
        "technical": technical_block,
        "percent": text("percent", 12),
        "one_liner": text("one_liner", 90) or "Listed on the label.",
        "what": what,
        "why": why,
        "watch": watch,
        "verdict": verdict,
        "position": position,
        "source": "ai",
    }


def _clean_nutrition(raw):
    if not isinstance(raw, dict):
        return {}

    allowed = {
        "energy", "fat", "saturated_fat", "trans_fat", "carbohydrates",
        "total_sugars", "added_sugars", "fiber", "protein", "sodium", "salt",
    }

    cleaned = {}

    for key, value in raw.items():
        if key not in allowed or not isinstance(value, dict):
            continue
        try:
            number = float(value.get("value"))
        except (TypeError, ValueError):
            continue
        if number < 0 or number > 100000:
            continue
        unit = str(value.get("unit", ""))[:6]
        cleaned[key] = {"value": number, "unit": unit}

    return cleaned


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def enrich_label(raw_text):
    """
    Returns:
        {
          "available": bool,       # did the AI pass succeed?
          "product_guess": str,
          "items": [ingredient cards],
          "nutrition": {...},
          "mode": "ai" | "cached" | "offline",
          "note": str              # why it fell back, if it did
        }
    """

    empty = {
        "available": False,
        "product_guess": "",
        "items": [],
        "nutrition": {},
        "mode": "offline",
        "note": "",
    }

    if not raw_text or not raw_text.strip():
        return empty

    if not AI_ENABLED:
        empty["note"] = "No ANTHROPIC_API_KEY set — using the offline ingredient table."
        return empty

    key = _cache_key(raw_text)
    cached = cache_get(key)

    if cached:
        cached["mode"] = "cached"
        return cached

    try:
        data = _call_api(raw_text)
    except urllib.error.HTTPError as error:
        empty["note"] = f"AI service returned {error.code} — using the offline ingredient table."
        return empty
    except Exception as error:
        empty["note"] = f"AI lookup unavailable ({error}) — using the offline ingredient table."
        return empty

    raw_items = data.get("ingredients")

    if not isinstance(raw_items, list) or not raw_items:
        empty["note"] = "AI pass found no readable ingredient list."
        return empty

    cleaned = [
        _clean_item(item, 0)
        for item in raw_items[:60]
        if isinstance(item, dict)
    ]
    items = [item for item in cleaned if item is not None]

    for position, item in enumerate(items, start=1):
        item["position"] = position

    if not items:
        empty["note"] = "AI pass returned only unreadable fragments."
        return empty

    product_guess = data.get("product_guess") or ""
    if not isinstance(product_guess, str):
        product_guess = ""

    result = {
        "available": True,
        "product_guess": product_guess.strip()[:80],
        "items": items,
        "nutrition": _clean_nutrition(data.get("nutrition")),
        "mode": "ai",
        "note": "",
    }

    cache_put(key, result)

    return result
