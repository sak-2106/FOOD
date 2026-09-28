"""
simplifier.py
=============

Two jobs:

1. OFFLINE FALLBACK — build ingredient cards from ingredients_db.json plus a
   set of pattern rules, for when the AI pass in enrich.py is unavailable.
   This is the safety net, not the primary path.

2. SHARED LOGIC — flags, nutrition wording and the 0-100 score. These run on
   top of whichever set of cards came back (AI or offline), so the front end
   always receives the same shape.
"""

import json
import os
import re

from quality import clean_ingredient_name, is_garbled
import knowledge

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "ingredients_db.json")

try:
    with open(DB_PATH, "r", encoding="utf-8") as handle:
        INGREDIENT_DB = json.load(handle)
except Exception:
    INGREDIENT_DB = {}


# ============================================================
# EMOJI + CATEGORY RULES
# ============================================================

EMOJI_RULES = [
    (r"(wheat|atta|flour|grain|maida|semolina|oat|barley|rye|millet|bajra|jowar)", "🌾"),
    (r"(sugar|syrup|fructose|dextrose|maltose|jaggery|honey|sucrose)", "🍬"),
    (r"(milk|dairy|whey|cheese|butter|cream|curd|paneer|casein)", "🥛"),
    (r"(oil|fat|ghee|palm|shortening|margarine)", "🫒"),
    (r"(salt|sodium chloride)", "🧂"),
    (r"(cocoa|chocolate)", "🍫"),
    (r"(peanut|almond|cashew|walnut|pistachio|hazelnut|\bnut\b)", "🥜"),
    (r"(soy|soya|lecithin)", "🫘"),
    (r"(rice|corn|maize|starch)", "🌽"),
    (r"(egg|albumen)", "🥚"),
    (r"(fruit|apple|mango|orange|berry|lemon|banana|date)", "🍎"),
    (r"(tomato|onion|garlic|potato|carrot|spinach|pea\b|vegetable)", "🥕"),
    (r"(spice|masala|pepper|chilli|chili|cumin|turmeric|coriander|ginger)", "🌶️"),
    (r"(flavour|flavor|essence|vanilla)", "👃"),
    (r"(colour|color|tartrazine|carmoisine|caramel|annatto)", "🎨"),
    (r"(preservative|benzoate|sorbate|antioxidant|tbhq|bht|bha|tocopherol)", "🧪"),
    (r"(acid|citric|regulator|raising|emulsif|stabilis|stabiliz|thicken|gum)", "⚗️"),
    (r"(vitamin|mineral|iron|calcium|zinc|niacin|folic)", "💊"),
    (r"(water|aqua)", "💧"),
    (r"(yeast|bread)", "🍞"),
    (r"\b(ins|e)\s?\d{3}", "🧪"),
]


def pick_emoji(*parts):
    haystack = " ".join(str(p or "") for p in parts).lower()
    for pattern, glyph in EMOJI_RULES:
        if re.search(pattern, haystack):
            return glyph
    return "🥄"


# Categories that deserve a "worth a look" chip on the card.
LOOK_CATEGORIES = {
    "allergen": "A common allergen — skip it if you react to this.",
    "sweetener": "This is added sugar, under a different name.",
    "preservative": "A preservative. Fine for most people, but it is a processing additive.",
    "artificial color": "An artificial colour, added purely for appearance.",
    "colour": "A colour added purely for appearance.",
    "flavor enhancer": "A flavour enhancer. Some people prefer to limit these.",
    "antioxidant": "Added to stop fats going rancid.",
    "food additive": "A processing additive rather than a food.",
}

ALLERGEN_WORDS = r"(milk|dairy|whey|casein|wheat|gluten|soy|soya|peanut|almond|cashew|nut|egg|fish|shellfish|sesame|mustard)"


def normalize_name(name):
    name = name.lower().strip()
    name = re.sub(r"\([^)]*\)", " ", name)
    name = re.sub(r"[^a-z0-9%\s-]", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def find_percent(text):
    match = re.search(r"(\d{1,3}(?:\.\d+)?)\s*%", text)
    return f"{match.group(1)}%" if match else ""


def declaration_scope(original):
    """
    Split an ingredient declaration into the part that describes the
    ingredient itself and the sub-list it may carry.

        "Multigrain Flour Mix (54.1%) (Wheat Flour (Atta) (36.1%), Oats...)"
            -> ("Multigrain Flour Mix (54.1%)", True)
        "Antioxidant (INS 307b)"
            -> ("Antioxidant (INS 307b)", False)

    A bracket holding a comma-separated list is a compound ingredient's own
    recipe; a bracket holding a percentage or an additive code is just part
    of the ingredient's name. The cut is made at the first bracket group
    that contains a comma, so a stated percentage stays with the name it
    belongs to while a sub-list is held back.
    """

    depth = 0
    group_start = None
    group_has_comma = False

    for index, character in enumerate(original):
        if character in "([{":
            if depth == 0:
                group_start = index
                group_has_comma = False
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
            if depth == 0 and group_has_comma and group_start is not None:
                return original[:group_start].strip(), True
        elif character == "," and depth > 0:
            group_has_comma = True

    # An unclosed bracket carrying a comma — common with OCR'd labels, where
    # the closing brace reads as something else entirely.
    if depth > 0 and group_has_comma and group_start is not None:
        return original[:group_start].strip(), True

    return original.strip(), False


def match_db(normalized):
    """Match on the key and on the aliases the DB already carries."""

    best = None
    best_length = 0

    for key, info in INGREDIENT_DB.items():
        candidates = [key] + [a.lower() for a in info.get("aliases", [])]

        for candidate in candidates:
            if not candidate:
                continue
            if re.search(rf"\b{re.escape(candidate)}\b", normalized):
                if len(candidate) > best_length:
                    best, best_length = info, len(candidate)

    return best


# Short, human one-liners for the card header. Truncating a textbook sentence
# reads badly, so offline mode writes its own short line per category.
CATEGORY_ONELINERS = {
    "sweetener": "Added sugar, plain and simple.",
    "allergen": "A common allergen.",
    "preservative": "Keeps it from spoiling.",
    "antioxidant": "Stops the fats going rancid.",
    "artificial color": "Added for colour only.",
    "colour": "Added for colour only.",
    "color": "Added for colour only.",
    "flavor enhancer": "Makes the taste stronger.",
    "flavouring": "Added for taste.",
    "food additive": "A processing additive.",
    "emulsifier": "Keeps oil and water mixed.",
    "grain": "A cereal flour.",
    "oil": "A cooking fat.",
    "dairy": "A milk ingredient.",
    "spice": "Added for flavour.",
    "protein": "A protein ingredient.",
}

NAME_ONELINERS = [
    (r"\bsalt\b", "Salt."),
    (r"\bwater\b", "Water."),
    (r"(flour|atta|maida)", "A cereal flour."),
    (r"(oil|ghee|butter)", "A cooking fat."),
    (r"(milk|whey|curd|cheese)", "A milk ingredient."),
    (r"(spice|masala|pepper|chilli)", "Added for flavour."),
]


def tidy_name(original):
    """'Multigrain Flour Mix (Atta) (36.1%)' -> 'Multigrain Flour Mix'."""

    name = re.sub(r"\([^)]*\)", " ", original)
    name = re.sub(r"\d{1,3}(?:\.\d+)?\s*%", " ", name)
    name = re.sub(r"[^A-Za-z0-9&'\-\s]", " ", name)
    name = re.sub(r"\s+", " ", name).strip(" -&")

    if len(name) < 2:
        name = re.sub(r"\s+", " ", original).strip()

    return name.title()[:60]


def first_sentence(text, limit=150):
    if not text:
        return ""
    parts = re.split(r"(?<=[.!?])\s", text.strip())
    return parts[0][:limit]


def shorten_words(text, max_words=9):
    words = re.sub(r"\s+", " ", (text or "").strip()).split(" ")
    if len(words) <= max_words:
        return " ".join(words)
    return " ".join(words[:max_words]).rstrip(",.;") + "…"


# ============================================================
# OFFLINE CARD BUILDER
# ============================================================

def simplify_ingredients(ingredients_list):
    """Fallback path: ingredients_db.json + rules → the same card shape the AI returns."""

    items = []

    for raw in ingredients_list:
        original = clean_ingredient_name(raw, max_len=90) or raw.strip()
        normalized = normalize_name(original)

        # "Choco Cream (Sugar, Edible Vegetable Oil (...), Emulsifier (INS
        # 322(i)), Flavour)" is one ingredient whose name is the bit before
        # the first bracket. Matching the whole declaration would name and
        # describe the card after whatever sits deepest in its sub-list —
        # this label's first ingredient was coming out as "Whole Oats Flour"
        # at 54.1% when it is really a multigrain flour mix.
        head, is_compound = declaration_scope(original)
        head_normalized = normalize_name(head)

        is_compound = is_compound and len(head_normalized) >= 4

        lookup_name = head_normalized if is_compound else normalized
        matched = match_db(lookup_name)

        # A code buried inside a compound's sub-list belongs to one of its
        # sub-ingredients, not to the ingredient itself.
        ins_source = head if is_compound else original
        ins_match = re.search(r"\b(?:INS|E)[\s-]?(\d{3,4}[a-z]?)\b", ins_source, re.IGNORECASE)

        if matched:
            simple_name = matched.get("label", "")
            printed = tidy_name(head if is_compound else original)

            # "Milk Solids" is more useful on screen than the DB's "Milk".
            if simple_name and simple_name.lower() in printed.lower() and len(printed) <= 40:
                simple_name = printed
            elif not simple_name:
                simple_name = printed

            category = matched.get("category", "Other")
            what = first_sentence(matched.get("what", ""))
            why = first_sentence(matched.get("why_used", ""))
            # The DB's "health" note is background info for every ingredient,
            # not a flag reason — plain salt and black pepper have a health
            # note too. Only LOOK_CATEGORIES / allergen matching below
            # should decide whether this gets a "worth a look" chip.
            watch_source = ""
            health_note = matched.get("health", "")

        elif ins_match:
            code = "INS " + ins_match.group(1).upper()

            # "Raising Agent (INS 500(ii))" reads better than a bare code.
            function = re.sub(r"\([^)]*\)", " ", ins_source)
            function = re.sub(r"\b(?:INS|E)\s?\d{3,4}[a-z]?\b", " ", function, flags=re.IGNORECASE)
            function = re.sub(r"[^A-Za-z\s]", " ", function)
            function = re.sub(r"\s+", " ", function).strip()

            simple_name = f"{function.title()} ({code})"[:60] if len(function) > 2 else code
            category = f"{function.title()}" if len(function) > 2 else "Food additive"

            # The label usually states the additive's job ("Emulsifier",
            # "Raising Agent") even when the exact compound is unnamed —
            # that's enough for a real explanation via the knowledge rules.
            function_rule = knowledge.lookup(function.lower(), original) if len(function) > 2 else None

            if function_rule:
                _, rule_what, rule_why, rule_watch = function_rule
                what = f"{rule_what} The label identifies it by the code {code}."
                why = rule_why
                watch_source = rule_watch or "An additive code. The label doesn't say how much is used."
            else:
                what = f"{code} is the additive code the label uses for this. The specific compound isn't identified beyond the code."
                why = "Added during processing rather than as a food in its own right."
                watch_source = "An additive code. The label doesn't say how much is used."

            health_note = ""

        else:
            simple_name = tidy_name(head if is_compound else original)
            rule = knowledge.lookup(lookup_name, head if is_compound else original)

            if rule:
                category, what, why, watch_source = rule
            else:
                category = "Other"
                what = ""
                why = ""
                watch_source = ""
            health_note = ""

        # A name that survives cleanup but is still noise once titled
        # (e.g. a lone OCR fragment with no real word in it) gets dropped
        # here rather than shown as a mystery ingredient.
        if not simple_name or is_garbled(simple_name, max_len=60):
            continue

        category_key = category.lower()
        watch = ""

        for needle, message in LOOK_CATEGORIES.items():
            if needle in category_key:
                watch = message
                break

        if not watch and re.search(ALLERGEN_WORDS, normalized):
            watch = "Contains a common allergen."

        if not watch and watch_source:
            watch = watch_source

        verdict = "look" if watch else "ok"

        if not what:
            what = "Listed by weight in the ingredients list. The specific role of this ingredient isn't in our reference data."

        # A benign DB health note ("Sugar provides energy but relatively
        # little micronutritional value...") adds real depth to the card
        # without turning an ordinary ingredient into a flagged one.
        if health_note and health_note not in what:
            what = f"{what} {first_sentence(health_note, 160)}".strip()

        one_liner = ""

        for needle, line in CATEGORY_ONELINERS.items():
            if needle in category_key:
                one_liner = line
                break

        if not one_liner:
            for pattern, line in NAME_ONELINERS:
                if re.search(pattern, normalized):
                    one_liner = line
                    break

        if not one_liner and ins_match:
            one_liner = "An additive, listed by its code."

        items.append(
            {
                "original": original,
                "simple_name": simple_name,
                "emoji": pick_emoji(simple_name, category, original),
                "category": category,
                "percent": find_percent(head if is_compound else original),
                "one_liner": one_liner,  # position-dependent default filled in below
                "what": what,
                "why": why,
                "watch": watch,
                "verdict": verdict,
                "source": "offline",
            }
        )

    # Positions are assigned after noise has been dropped, so "listed 3rd"
    # always matches what's actually on screen.
    for position, item in enumerate(items, start=1):
        item["position"] = position
        if not item["one_liner"]:
            item["one_liner"] = (
                "The main ingredient by weight." if position == 1
                else f"Listed at position {position} by weight."
            )

    return {"items": items, "flags": build_flags(items)}


# ============================================================
# FLAGS (runs on AI cards too)
# ============================================================

def build_flags(items):
    flags = {"allergens": [], "additives": [], "sweeteners": []}

    for item in items:
        category = (item.get("category") or "").lower()
        name = item.get("simple_name") or item.get("original") or ""
        normalized = normalize_name(f"{name} {item.get('original','')}")

        if "allergen" in category or re.search(ALLERGEN_WORDS, normalized):
            bucket = "allergens"
        elif "sweeten" in category or re.search(r"(sugar|syrup|fructose|dextrose|maltose)", normalized):
            bucket = "sweeteners"
        elif any(
            word in category
            for word in (
                "preserv", "colour", "color", "additive", "antioxidant", "enhancer",
                "emulsif", "stabilis", "stabiliz", "agent", "regulator", "thicken", "anticaking",
            )
        ) or re.search(r"\b(?:INS|E)\s?\d{3,4}[a-z]?\b", item.get("original", ""), re.IGNORECASE):
            bucket = "additives"
        else:
            continue

        if name and name not in flags[bucket]:
            flags[bucket].append(name)

    return flags


# ============================================================
# NUTRITION WORDING
# ============================================================

NUTRITION_LABELS = {
    "energy": "Energy",
    "fat": "Total fat",
    "saturated_fat": "Saturated fat",
    "trans_fat": "Trans fat",
    "carbohydrates": "Carbohydrates",
    "total_sugars": "Total sugars",
    "added_sugars": "Added sugars",
    "fiber": "Fibre",
    "protein": "Protein",
    "sodium": "Sodium",
    "salt": "Salt",
}

DEFAULT_UNITS = {"energy": "kcal", "sodium": "mg"}

# ------------------------------------------------------------
# India's own daily reference values (FSSAI Labelling & Display
# Regulations 2020, Schedule / %RDA column: 2000 kcal, 67 g total fat,
# 22 g saturated fat, 2 g trans fat, 50 g added sugar, 2000 mg sodium
# for an average adult per day). Protein and fibre use the ICMR-NIN
# Dietary Guidelines for Indians, 2024 reference intakes for an adult.
# This is the same basis already printed as the "%RDA" column on most
# Indian packs, not a number FoodLens invented.
# ------------------------------------------------------------

FSSAI_DAILY_VALUES = {
    "fat": 67,            # g
    "saturated_fat": 22,  # g
    "trans_fat": 2,       # g
    "sugar": 50,           # g (FSSAI's daily value is for added sugar)
    "sodium": 2000,        # mg
    "protein": 55,          # g, ICMR-NIN reference adult RDA
    "fiber": 30,            # g, ICMR-NIN recommended daily intake
}

# Common "20 / 5" convention used on %RDA-style labels: >=20% of a day's
# value in 100 g counts as "high", <=5% counts as "low". This is the same
# rule FSSAI itself uses to define a "rich source" (>=20% RDA) versus a
# "source" (>=10% RDA) claim under the Advertising & Claims Regulations.
_HIGH_DV = 20.0
_MODERATE_DV = 5.0
_RICH_SOURCE_DV = 20.0
_SOURCE_DV = 10.0


def _dv_percent(value, key):
    daily_value = FSSAI_DAILY_VALUES.get(key)
    if not daily_value:
        return None
    return (value / daily_value) * 100


def _dv_level(pct):
    if pct >= _HIGH_DV:
        return "High"
    if pct >= _MODERATE_DV:
        return "Moderate"
    return "Low"


def daily_value_breakdown(nutrition_dict):
    """
    Per-nutrient share of an Indian adult's daily reference value, per
    100 g of the product — the same %RDA basis already printed on the
    pack, not a FoodLens-invented scale.

    Returns a dict keyed by "saturated_fat", "trans_fat", "sugar",
    "sodium", "protein", "fiber" — whichever were actually read — each
    holding {value, unit, pct_dv, level, approx}. "sugar" prefers
    added_sugars (what the FSSAI daily value is actually defined
    against); when only total_sugars was read, it's used instead and
    marked approx=True, since total sugar is somewhat higher than added
    sugar for products with naturally occurring sugars (milk, fruit).
    """

    nutrition_dict = nutrition_dict or {}
    breakdown = {}

    sugar_key = "added_sugars" if "added_sugars" in nutrition_dict else "total_sugars"
    sugar_entry = nutrition_dict.get(sugar_key)
    if sugar_entry:
        value = sugar_entry.get("value", 0)
        pct = _dv_percent(value, "sugar")
        breakdown["sugar"] = {
            "value": value, "unit": "g", "pct_dv": round(pct, 1),
            "level": _dv_level(pct), "approx": sugar_key == "total_sugars",
        }

    for key in ("saturated_fat", "trans_fat", "sodium"):
        entry = nutrition_dict.get(key)
        if not entry:
            continue
        value = entry.get("value", 0)
        pct = _dv_percent(value, key)
        breakdown[key] = {
            "value": value, "unit": entry.get("unit") or DEFAULT_UNITS.get(key, "g"),
            "pct_dv": round(pct, 1), "level": _dv_level(pct), "approx": False,
        }

    for key in ("protein", "fiber"):
        entry = nutrition_dict.get(key)
        if not entry:
            continue
        value = entry.get("value", 0)
        pct = _dv_percent(value, key)
        breakdown[key] = {
            "value": value, "unit": "g", "pct_dv": round(pct, 1),
            "level": "Rich source" if pct >= _RICH_SOURCE_DV else "Source" if pct >= _SOURCE_DV else "Present",
            "approx": False,
        }

    return breakdown


def simplify_nutrition(nutrition_dict):
    items = []
    summary = []

    if not nutrition_dict:
        return {"items": items, "summary": summary, "daily_values": {}}

    for key, data in nutrition_dict.items():
        items.append(
            {
                "key": key,
                "nutrient": NUTRITION_LABELS.get(key, key.replace("_", " ").title()),
                "value": data.get("value"),
                "unit": data.get("unit") or DEFAULT_UNITS.get(key, "g"),
            }
        )

    breakdown = daily_value_breakdown(nutrition_dict)

    def add(nutrient, level, message):
        summary.append({"nutrient": nutrient, "level": level, "message": message})

    sugar = breakdown.get("sugar")
    if sugar:
        note = " (from total sugar; added sugar wasn't printed separately)" if sugar["approx"] else ""
        add(
            "Sugar", sugar["level"],
            f"{sugar['value']:g} g per 100 g — {sugar['pct_dv']:g}% of a day's added-sugar limit{note}.",
        )

    saturated = breakdown.get("saturated_fat")
    if saturated:
        add(
            "Saturated fat", saturated["level"],
            f"{saturated['value']:g} g per 100 g — {saturated['pct_dv']:g}% of a day's limit.",
        )

    trans = breakdown.get("trans_fat")
    if trans:
        add(
            "Trans fat", trans["level"],
            f"{trans['value']:g} g per 100 g — {trans['pct_dv']:g}% of a day's limit.",
        )

    sodium = breakdown.get("sodium")
    if sodium:
        add(
            "Sodium", sodium["level"],
            f"{sodium['value']:g} mg per 100 g — {sodium['pct_dv']:g}% of a day's limit.",
        )

    fiber = breakdown.get("fiber")
    if fiber:
        tag = "Present" if fiber["level"] == "Present" else "Present"  # always renders as "Present" tag
        add(
            "Fibre", tag,
            f"{fiber['value']:g} g per 100 g — {fiber['pct_dv']:g}% of a day's intake"
            f"{', a rich source' if fiber['level'] == 'Rich source' else ', a source' if fiber['level'] == 'Source' else ''}.",
        )

    protein = breakdown.get("protein")
    if protein:
        add(
            "Protein", "Present",
            f"{protein['value']:g} g per 100 g — {protein['pct_dv']:g}% of a day's intake"
            f"{', a rich source' if protein['level'] == 'Rich source' else ', a source' if protein['level'] == 'Source' else ''}.",
        )

    return {"items": items, "summary": summary, "daily_values": breakdown}


# ============================================================
# SCORE
# ============================================================
#
# Built from India's own %RDA reference values (see FSSAI_DAILY_VALUES
# above) instead of a fixed starting number, so two products land at
# genuinely different scores based on how far over or under the daily
# value they actually are — not just which fixed bracket they cleared.
#
# The one rule this enforces on purpose: a product that's genuinely
# "High" in a nutrient of concern (>=20% of the daily value per 100 g)
# cannot be pulled back up into "Good choice" territory by protein or
# fibre. That cancel-out is the specific flaw critics point to in
# Australia's Health Star Rating, which FSSAI's own 2022 draft was
# modelled on — a product high in sugar or saturated fat can still
# score well if it also has fibre or protein. Here, positive nutrients
# can soften the score a little, but a capped ceiling stops them from
# erasing a real red flag.

_NEGATIVE_WEIGHTS = {"saturated_fat": 1.3, "trans_fat": 1.5, "sugar": 1.2, "sodium": 1.0}
_POSITIVE_WEIGHTS = {"protein": 0.35, "fiber": 0.35}
_POSITIVE_CAP = 10          # positive nutrients can add at most this many points
_RISK_DIVISOR = 4.0          # scales the weighted %RDA sum down to a 0-100 range
_CEILING_ONE_HIGH = 60       # score cap when exactly one nutrient is "High"
_CEILING_TWO_PLUS_HIGH = 40  # score cap when two or more nutrients are "High"

_NUTRIENT_WORDS = {
    "saturated_fat": "saturated fat", "trans_fat": "trans fat",
    "sugar": "sugar", "sodium": "sodium", "protein": "protein", "fiber": "fibre",
}


def calculate_food_score(nutrition_dict, ingredient_items):
    """
    A 0-100 'how does this label look' heuristic, built from FSSAI's own
    %RDA reference values rather than a fixed starting score. Not a
    medical or regulatory rating.

    Returns {"score", "label", "reasons" (bullet list), "note" (one
    plain-English sentence naming what actually drove the score)}.
    """

    nutrition_dict = nutrition_dict or {}
    ingredient_items = ingredient_items or []
    breakdown = daily_value_breakdown(nutrition_dict)

    if not breakdown and not ingredient_items:
        return {
            "score": None,
            "label": "Not scored",
            "reasons": ["Not enough readable label data to judge this one."],
            "note": "Not enough readable label data to judge this one.",
        }

    # A score built only from the ingredient list, with no nutrition table
    # at all, would silently treat every nutrient as "fine" by default —
    # that's not neutral, it's unknown, and unknown shouldn't score as
    # good. So this doesn't produce a 0-100 number here; it still surfaces
    # what the ingredient list alone tells us.
    if not breakdown:
        flags = build_flags(ingredient_items)
        additives = len(flags["additives"])
        sweeteners = len(flags["sweeteners"])
        allergens = len(flags["allergens"])
        count = len(ingredient_items)

        reasons = []
        if additives:
            reasons.append(f"{additives} additive{'s' if additives > 1 else ''} or preservative{'s' if additives > 1 else ''} on the list.")
        if sweeteners >= 2:
            reasons.append("Sugar appears more than once, under different names.")
        if allergens:
            reasons.append(f"Contains {allergens} common allergen{'s' if allergens > 1 else ''}.")
        if count > 15:
            reasons.append(f"A long list — {count} ingredients means heavily processed.")
        elif 0 < count <= 5:
            reasons.append(f"A short list — only {count} ingredients.")
        reasons.append("No nutrition table was read, so sugar, fat and sodium levels are unknown.")

        return {
            "score": None,
            "label": "Not scored",
            "reasons": reasons,
            "note": (
                "No score: the nutrition table wasn't read, so there's no basis to judge sugar, "
                "fat or sodium content. A score from the ingredient list alone would be a guess."
            ),
        }

    reasons = []
    risk_terms = []   # (key, weighted_contribution) for negatives
    positive_terms = []  # (key, weighted_contribution) for positives
    high_count = 0

    for key, weight in _NEGATIVE_WEIGHTS.items():
        entry = breakdown.get(key)
        if not entry:
            continue
        pct = entry["pct_dv"]
        contribution = weight * pct
        risk_terms.append((key, contribution))

        word = _NUTRIENT_WORDS[key]
        if entry["level"] == "High":
            high_count += 1
            reasons.append(f"High in {word} — {pct:g}% of a day's FSSAI reference value, per 100 g.")
        elif entry["level"] == "Moderate":
            reasons.append(f"A moderate amount of {word} — {pct:g}% of a day's value, per 100 g.")
        else:
            reasons.append(f"Low in {word} — {pct:g}% of a day's value, per 100 g.")

    for key, weight in _POSITIVE_WEIGHTS.items():
        entry = breakdown.get(key)
        if not entry:
            continue
        pct = entry["pct_dv"]
        positive_terms.append((key, weight * pct))
        word = _NUTRIENT_WORDS[key]
        if entry["level"] == "Rich source":
            reasons.append(f"A rich source of {word} — {pct:g}% of a day's intake, per 100 g.")
        elif entry["level"] == "Source":
            reasons.append(f"A source of {word} — {pct:g}% of a day's intake, per 100 g.")

    risk_points = sum(c for _, c in risk_terms) / _RISK_DIVISOR
    positive_points = min(sum(c for _, c in positive_terms), _POSITIVE_CAP)

    flags = build_flags(ingredient_items)
    additives = len(flags["additives"])
    sweeteners = len(flags["sweeteners"])
    allergens = len(flags["allergens"])
    count = len(ingredient_items)

    ingredient_penalty = 0.0

    if additives:
        ingredient_penalty += min(additives, 6) * 4
        reasons.append(f"{additives} additive{'s' if additives > 1 else ''} or preservative{'s' if additives > 1 else ''} on the list.")

    if sweeteners >= 2:
        ingredient_penalty += 8
        reasons.append("Sugar appears more than once, under different names.")

    if allergens:
        reasons.append(f"Contains {allergens} common allergen{'s' if allergens > 1 else ''}.")

    if count > 15:
        ingredient_penalty += 5
        reasons.append(f"A long list — {count} ingredients means heavily processed.")
    elif 0 < count <= 5:
        ingredient_penalty -= 5
        reasons.append(f"A short list — only {count} ingredients.")

    raw_score = 100 - risk_points + positive_points - ingredient_penalty

    # The rule that stops fibre/protein from erasing a real red flag.
    if high_count >= 2:
        score = min(raw_score, _CEILING_TWO_PLUS_HIGH)
    elif high_count == 1:
        score = min(raw_score, _CEILING_ONE_HIGH)
    else:
        score = raw_score

    score = round(max(0, min(100, score)))

    if score >= 75:
        label = "Good choice"
    elif score >= 50:
        label = "Okay"
    else:
        label = "Worth a closer look"

    # ---- the note: name whichever one or two things actually drove it ----
    # Only nutrients actually flagged Moderate/High are "drivers" — a Low
    # nutrient shouldn't be named as something that pulled the score down
    # just because its contribution wasn't exactly zero.
    flagged = [
        (key, contribution) for key, contribution in risk_terms
        if breakdown.get(key, {}).get("level") in ("Moderate", "High")
    ]
    drivers = sorted(flagged, key=lambda pair: pair[1], reverse=True)
    top_negative = [_NUTRIENT_WORDS[k] for k, c in drivers[:2]]
    top_positive = max(positive_terms, key=lambda pair: pair[1], default=None)
    good_positive = (
        top_positive
        if top_positive and breakdown.get(top_positive[0], {}).get("level") in ("Source", "Rich source")
        else None
    )

    parts = []
    if top_negative:
        named = top_negative[0] if len(top_negative) == 1 else " and ".join(top_negative)
        parts.append(f"pulled down mainly by {named}")
    if additives >= 3:
        parts.append(f"{additives} additives")
    if high_count >= 2:
        parts.append("capped because more than one nutrient is genuinely high")
    elif high_count == 1:
        parts.append("capped because one nutrient is genuinely high")

    note = "Score " + (", ".join(parts) if parts else "close to neutral — nothing on the label stood out either way") + "."

    if good_positive and high_count == 0:
        note += f" {_NUTRIENT_WORDS[good_positive[0]].capitalize()} content helped a little."
    elif good_positive:
        note += (
            f" {_NUTRIENT_WORDS[good_positive[0]].capitalize()} content softened it slightly, "
            "but couldn't outweigh the flagged nutrient."
        )

    if not reasons:
        reasons.append("Not enough readable label data to judge this one.")

    return {"score": score, "label": label, "reasons": reasons, "note": note}
