"""
parser.py
=========

Pulls the ingredient list and nutrition table out of raw OCR text using
regular expressions. This runs before the AI pass in enrich.py and is also
what the offline mode relies on entirely.

Two fixes over the previous version:

- Splitting is bracket-aware, so "Antioxidant (INS 307b, INS 319)" stays as
  one ingredient instead of being chopped at the comma inside the brackets.
- Fragments that are mostly digits or symbols are dropped instead of being
  shown as ingredients.
"""

import re

from quality import clean_ingredient_name, presplit_missed_commas

SECTION_END = (
    r"allergen|contains|storage|best before|net (?:weight|qty|quantity)|"
    r"nutrition|manufactured|marketed|customer care|fssai|mrp|batch|"
    r"per\s*100\s*g|serving size|energy\s*\d|approximate value"
)


def clean_text(text):
    text = text.replace("\r", " ")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def normalize_ocr(text):
    """Nudge the commonest OCR spellings back into shape."""

    fixes = [
        (r"ingred[il1]ents?\s*[:\-–]?", "INGREDIENTS:"),
        (r"nutrition(?:al)?\s+(?:information|facts)", "NUTRITION INFORMATION"),
        (r"\bINS[\s.]?(\d)", r"INS \1"),
        (r"\b0g\b", "0 g"),
        (r"(\d)\s*[gG]\b", r"\1 g"),
        (r"(\d)\s*(?:mg|MG|mG)\b", r"\1 mg"),
        (r"(\d)\s*(?:kcal|KCAL|Kcal)\b", r"\1 kcal"),
        # Common Tesseract misreads of nutrition-table header words on
        # small/dense grid text — caught here rather than made stricter
        # upstream, since the OCR pass has no way to know these letters
        # are wrong; only the fact that they're expected label words does.
        (r"\ber+ergy\b", "energy"),
        (r"\bp\s*[o0]tein\b", "protein"),
        (r"\bsa[wv]?[iu]?[nu]m?\s*\(m?g\)", "sodium (mg)"),
    ]

    for pattern, replacement in fixes:
        text = re.sub(pattern, replacement, text, flags=re.IGNORECASE)

    return text


# ============================================================
# INGREDIENTS
# ============================================================

def split_ingredients(text):
    """
    Split on commas and semicolons that sit outside brackets — except a
    bracket opened by OCR noise sometimes never closes, which would
    otherwise swallow every ingredient after it into one fragment. If a
    bracket has stayed open for an implausibly long stretch, give up on
    tracking it so later commas can still split normally.

    That threshold has to stay well above the length of a real compound
    ingredient declaration. FSSAI-style labels commonly wrap a whole
    sub-list in one bracket — e.g. "CHOCO CREAM {SUGAR, EDIBLE VEGETABLE
    OIL (SUNFLOWER OIL, PALM OIL), COCOA SOLIDS, MILK SOLIDS, EMULSIFIER
    (INS 322(i)), FLAVOUR (NATURAL)}" — which can easily run past 150
    characters on its own. Too low a threshold gives up on a bracket like
    that halfway through, so the top-level commas inside it start
    splitting one ingredient into several fake ones.
    """

    parts = []
    buffer = []
    depth = 0
    chars_since_open = 0

    for character in text:
        if character in "([{":
            if depth == 0:
                chars_since_open = 0
            depth += 1
        elif character in ")]}":
            depth = max(0, depth - 1)
            if depth == 0:
                chars_since_open = 0
        elif depth > 0:
            chars_since_open += 1
            if chars_since_open > 280:
                depth = 0  # this bracket is never closing — stop honoring it

        if character in ",;" and depth == 0:
            parts.append("".join(buffer))
            buffer = []
        else:
            buffer.append(character)

    parts.append("".join(buffer))

    return parts


def looks_like_junk(item):
    # Kept for anything importing it elsewhere; real filtering now happens
    # in quality.clean_ingredient_name, which is stricter about OCR noise.
    stripped = item.strip()

    if len(stripped) < 3 or len(stripped) > 90:
        return True

    letters = sum(character.isalpha() for character in stripped)

    if letters < 3:
        return True

    if letters / len(stripped) < 0.45:
        return True

    if stripped.lower() in {
        "ingredients", "ingredient", "nutrition", "information",
        "contains", "allergen", "allergens", "and", "or", "may contain",
    }:
        return True

    return False


def extract_ingredients(text):
    text = normalize_ocr(text)

    match = re.search(
        rf"ingredients?\s*:?\s*(.*?)(?={SECTION_END}|$)",
        text,
        re.IGNORECASE | re.DOTALL,
    )

    if not match:
        return []

    block = re.sub(r"\s+", " ", match.group(1)).strip()
    block = re.split(SECTION_END, block, flags=re.IGNORECASE)[0]

    # A missed comma prints as a period — recover it before splitting, so
    # "Antioxidant (INS 307b). Natural Flavour" splits into two ingredients
    # instead of merging into one garbled string.
    block = presplit_missed_commas(block)

    ingredients = []

    for raw in split_ingredients(block):
        # A compound ingredient's own declared sub-list ("Choco Cream
        # {Sugar, Edible Vegetable Oil..., Emulsifier..., Flavour...}")
        # is one real ingredient but can easily run well past a normal
        # ingredient name's length — don't let the garbled-text length
        # check throw the whole thing away for being long.
        cleaned = clean_ingredient_name(raw, max_len=200)

        if not cleaned:
            continue

        skip_words = {
            "ingredients", "ingredient", "nutrition", "information",
            "contains", "allergen", "allergens", "and", "or", "may contain",
        }

        if cleaned.lower() in skip_words:
            continue

        ingredients.append(cleaned)

    return ingredients[:60]


# ============================================================
# NUTRITION
# ============================================================

# The unit used to be required right after the number (e.g. "6.42g"), which
# matches labels that print it that way — but plenty of Indian nutrition
# tables (including gridded "Per 100g / Per Serve / %RDA" ones) print the
# unit once, next to the nutrient name ("Total Fat (g)"), and leave every
# value in the row bare. Against that format the old patterns never matched
# at all, so the whole table silently came back empty. The unit is now
# optional after the number; DEFAULT_UNITS below fills it in when the
# pattern matches without one.
# `_UNIT_TAG` optionally eats a short "(g)"-style annotation right after the
# nutrient name before the search for the value begins. Without it, an OCR
# misread of that annotation — "(g)" -> "(4)" is a real, observed case —
# hands the digit inside the parenthesis to \D{0,18}? as if it were the
# start of the value, and the real number later in the row never gets
# captured at all.
_UNIT_TAG = r"\s*(?:\([^)]{0,6}\))?\s*"

NUTRIENT_PATTERNS = {
    "energy": [rf"energy{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(kcal|kj)?"],
    "fat": [
        rf"total\s+fat{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?",
        rf"(?<!saturated )(?<!trans )\bfat{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?",
    ],
    "saturated_fat": [rf"saturated\s*(?:fat|fatty\s*acids?)?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "trans_fat": [rf"trans\s*(?:fat|fatty\s*acids?)?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "carbohydrates": [rf"carbohydrates?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "total_sugars": [
        rf"total\s+sugars?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?",
        rf"(?<!added )\bsugars?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?",
    ],
    "added_sugars": [rf"added\s+sugars?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "fiber": [rf"(?:dietary\s+)?fibr?e?{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "protein": [rf"protein{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
    "sodium": [rf"sodium{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(mg)?"],
    "salt": [rf"salt{_UNIT_TAG}\D{{0,18}}?(\d+(?:\.\d+)?)\s*(g)?"],
}

DEFAULT_UNITS = {
    "energy": "kcal",
    "fat": "g",
    "saturated_fat": "g",
    "trans_fat": "g",
    "carbohydrates": "g",
    "total_sugars": "g",
    "added_sugars": "g",
    "fiber": "g",
    "protein": "g",
    "sodium": "mg",
    "salt": "g",
}

SANE_MAX = {
    "energy": 2000,
    "fat": 100,
    "saturated_fat": 100,
    "trans_fat": 100,
    "carbohydrates": 100,
    "total_sugars": 100,
    "added_sugars": 100,
    "fiber": 100,
    "protein": 100,
    "sodium": 50000,
    "salt": 100,
}


def extract_nutrition(text):
    text = normalize_ocr(text)
    nutrition = {}

    for nutrient, patterns in NUTRIENT_PATTERNS.items():
        found = False

        # Two passes: first only accept a match whose captured number has a
        # decimal point, then relax to bare integers. Nearly every real
        # value on these labels is non-integer ("7.10", "21.39"...), so an
        # integer match is usually a decimal point OCR dropped, not a
        # genuine whole number — e.g. a garbled "7.10" easily comes back
        # as "70". Silently accepting that as 70 g would be confidently
        # wrong instead of honestly missing, which is worse for something
        # people are trusting for nutrition info. Preferring decimal
        # matches first means a bare integer is only ever used when no
        # candidate anywhere recovered the decimal point at all.
        for require_decimal in (True, False):
            if found:
                break

            for pattern in patterns:
                for match in re.finditer(pattern, text, re.IGNORECASE):
                    raw_value = match.group(1)

                    if require_decimal and "." not in raw_value:
                        continue

                    try:
                        value = float(raw_value)
                    except (TypeError, ValueError):
                        continue

                    # Reject OCR misreads like "Protein 480 g".
                    if value < 0 or value > SANE_MAX.get(nutrient, 100):
                        continue

                    unit = ""
                    if match.lastindex and match.lastindex >= 2:
                        unit = (match.group(2) or "").lower()

                    if not unit:
                        unit = DEFAULT_UNITS.get(nutrient, "")

                    nutrition[nutrient] = {"value": value, "unit": unit}
                    found = True
                    break

                if found:
                    break

    return nutrition


# ============================================================
# MAIN
# ============================================================

def parse_label(raw_text):
    raw_text = clean_text(raw_text)

    return {
        "ingredients": extract_ingredients(raw_text),
        "nutrition": extract_nutrition(raw_text),
        "raw_text": raw_text,
    }
