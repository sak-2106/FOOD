"""
quality.py
==========

Shared text cleanup used by both the offline parser and the AI enrichment
path, so a garbled OCR fragment gets the same treatment no matter which
route produced it.

Three problems this solves, all visible on real, imperfect label photos:

1. A missed comma. OCR frequently prints a period where the label had a
   comma, so "Antioxidant (INS 307b), Natural Flavour" comes back as
   "Antioxidant (INS 307b). Natural Flavour" and the two ingredients never
   get split apart.

2. Nutrition-table bleed. Ingredients and the nutrition panel sit right
   next to each other on most packs, and OCR often reads across the
   boundary, so a chunk like "Cocoa Solids... Saturated Fatty Acids 6%
   Iodised Salt" is really two unrelated things glued together.

3. Stray symbols and short nonsense tokens from low-contrast or glossy
   photos — "<>", "--", "°", "←", "V'Ta*'" — that aren't a real ingredient
   at all and shouldn't be shown as one.
"""

import re

# ============================================================
# NOISE
# ============================================================

STRAY_SYMBOLS = re.compile(r"[<>=_{}^~`|•¤¦§©®™¢£¥←→↑↓°!]+")
SMART_QUOTES = re.compile(r"[“”‘’„‟«»]")
REPEAT_PUNCT = re.compile(r"[\"'*]{1,}")
DASH_RUN = re.compile(r"-{2,}")

NUTRITION_WORDS = re.compile(
    r"\b("
    r"energy|kcal|k\s?cal|total\s*fat|saturated\s*fat(?:ty)?|trans\s*fat|"
    r"carbohydrate|total\s*sugars?|added\s*sugars?|protein|sodium|"
    r"dietary\s*fib(?:re|er)|per\s*100\s*g|serving\s*size|"
    r"nutrition(?:al)?\s*(?:information|facts|value)"
    r")\b",
    re.IGNORECASE,
)


def strip_noise(text):
    text = SMART_QUOTES.sub("'", text)
    text = DASH_RUN.sub(" ", text)
    text = STRAY_SYMBOLS.sub(" ", text)
    text = REPEAT_PUNCT.sub("", text)
    text = re.sub(r"\(\s*\)", " ", text)  # empty brackets left behind
    text = re.sub(r"\s+", " ", text).strip(" .,:;-")
    return text


def presplit_missed_commas(text):
    """
    OCR turning a comma into a period usually looks like ") . Capital" or
    "%). Capital" — a sentence-ending period would not normally sit right
    after a closing bracket or a percentage mid-ingredient-list. Treat it
    as a comma instead so the next fragment splits out on its own.
    """

    return re.sub(r"(?<=[%\)\d])\.\s+(?=[A-Z])", ", ", text)


def truncate_at_nutrition(text):
    """Cut a fragment off at the point a nutrition-table word appears."""

    match = NUTRITION_WORDS.search(text)
    if not match:
        return text
    return text[: match.start()]


# ============================================================
# GARBLED-FRAGMENT DETECTION
# ============================================================

def is_garbled(text, max_len=70):
    """
    True when a fragment reads like OCR noise rather than an ingredient
    name: too long to be a single ingredient, or built mostly from short
    tokens that mix letters and digits, or single stray characters.

    Two carve-outs matter here, because otherwise this rejects completely
    normal ingredient-list content, not just OCR noise:

    - A lone digit is common and meaningful on a label — "(5%)" reduces to
      the core token "5" once punctuation is stripped. That's a real
      quantity, not a stray character like a misread "l" or "¤".
    - A short run of digits with a trailing lowercase letter or two, or a
      letter followed by digits, is the normal shape of a food-additive or
      vitamin code — INS/E-numbers like "307b", "150d", "322(i)", and
      vitamin names like "B12", "D3", "K2" all mix letters and digits in
      four characters or fewer. None of that is garbled.
    """

    if not text or len(text) > max_len:
        return True

    words = text.split()
    if not words:
        return True

    junky = 0

    for word in words:
        core = re.sub(r"[^A-Za-z0-9]", "", word)

        if not core:
            junky += 1
            continue

        has_digit = any(character.isdigit() for character in core)
        has_alpha = any(character.isalpha() for character in core)

        if has_digit and has_alpha and len(core) <= 4:
            looks_like_additive_or_vitamin_code = (
                re.fullmatch(r"\d{2,4}[a-zA-Z]{0,2}", core)
                or re.fullmatch(r"[A-Za-z]{1,2}\d{1,3}[a-zA-Z]{0,2}", core)
            )
            if not looks_like_additive_or_vitamin_code:
                junky += 1
        elif len(core) == 1 and not core.isdigit() and core.lower() not in ("a", "i"):
            junky += 1

    return (junky / len(words)) > 0.3


# ============================================================
# PUBLIC ENTRY POINT
# ============================================================

def clean_ingredient_name(text, max_len=70):
    """
    Full pipeline for a single candidate ingredient string. Returns "" if
    what's left after cleanup doesn't look like a real ingredient — the
    caller should drop that entry rather than show it.
    """

    if not text:
        return ""

    text = strip_noise(text)
    text = truncate_at_nutrition(text)
    text = strip_noise(text)

    if is_garbled(text, max_len=max_len):
        return ""

    return text[:max_len].strip(" .,:;-")
