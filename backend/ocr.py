"""
ocr.py
======

Reads text off a label photo.

Change from the previous version: it used to run four preprocessing variants
and concatenate every unique line from all of them. That multiplied OCR noise —
one blurry ingredient turned into four different garbled spellings, all of which
ended up on screen. Now each variant is scored by Tesseract's own word
confidence and only the best one is kept.

Second change (this version): a single whole-image OCR pass was silently
dropping the nutrition table. It isn't a blur or lighting problem — the
prose (ingredients, description) and the nutrition grid need opposite
treatment. Prose is large, sparse, and reads fine as one uniform block
(psm 6) or in columns (psm 4). The nutrition grid is small, dense, and
tightly gridded, so whichever single variant/config wins the whole-image
vote (scored mostly by the much longer prose text) is usually a bad fit
for the grid, and Tesseract just fails to place most of its digits.
`extract_text` now runs that whole-image pass as before for prose, and
separately locates and re-OCRs the nutrition table region on its own
terms, then appends the reconstructed table lines to the output.
"""

import difflib
import os
import re
import shutil

import cv2
import numpy as np
import pytesseract

# ============================================================
# TESSERACT LOCATION
# ============================================================
# Set TESSERACT_CMD in the environment to override. Falls back to whatever is
# on PATH, then to the usual Windows install location.

_explicit = os.environ.get("TESSERACT_CMD")
_on_path = shutil.which("tesseract")
_windows_default = r"C:\Program Files\Tesseract-OCR\tesseract.exe"

if _explicit:
    pytesseract.pytesseract.tesseract_cmd = _explicit
elif _on_path:
    pytesseract.pytesseract.tesseract_cmd = _on_path
elif os.path.exists(_windows_default):
    pytesseract.pytesseract.tesseract_cmd = _windows_default


# ============================================================
# PREPROCESSING
# ============================================================

def preprocess_variants(image_path):
    """Label photos come with shadows, gloss, curves and low contrast."""

    image = cv2.imread(image_path)

    if image is None:
        raise ValueError("That image couldn't be opened.")

    height, width = image.shape[:2]

    if width < 1800:
        scale = 1800 / width
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif width > 3200:
        scale = 3200 / width
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Even out uneven lighting before anything else.
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    balanced = clahe.apply(gray)

    denoised = cv2.bilateralFilter(balanced, 7, 55, 55)

    _, otsu = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    adaptive = cv2.adaptiveThreshold(
        denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 11
    )

    sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(balanced, -1, sharpen_kernel)

    return [
        ("balanced", balanced),
        ("denoised", denoised),
        ("otsu", otsu),
        ("adaptive", adaptive),
        ("sharpened", sharpened),
    ]


# ============================================================
# SCORING
# ============================================================

def score_variant(image, config):
    """Mean word confidence, weighted by how much real text came back."""

    try:
        data = pytesseract.image_to_data(
            image, config=config, output_type=pytesseract.Output.DICT
        )
    except Exception:
        return 0.0, ""

    words = []
    confidences = []

    for text, confidence in zip(data["text"], data["conf"]):
        text = (text or "").strip()
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        if text and confidence > 0:
            words.append(text)
            confidences.append(confidence)

    if not confidences:
        return 0.0, ""

    mean_confidence = sum(confidences) / len(confidences)

    # A high-confidence read of three words is worse than a decent read of 120.
    volume_bonus = min(len(words) / 80.0, 1.0)

    return mean_confidence * (0.55 + 0.45 * volume_bonus), " ".join(words)


# ============================================================
# CLEANUP
# ============================================================

# Characters Tesseract invents on glossy packaging.
NOISE_CHARS = r"[|~^`¥©®™«»¢£§¤°¬¦]"


def clean_ocr_text(text):
    text = text.replace("\r", "\n")
    text = re.sub(NOISE_CHARS, " ", text)

    lines = []

    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()

        if not line:
            continue

        letters = sum(character.isalpha() for character in line)

        # Drop lines that are mostly symbols — barcodes, borders, gloss.
        if len(line) > 3 and letters / len(line) < 0.35:
            continue

        # Drop stray single characters.
        if len(line) < 2:
            continue

        lines.append(line)

    return "\n".join(lines)


# ============================================================
# NUTRITION TABLE — DEDICATED PASS
# ============================================================
#
# A whole-image OCR pass scores itself on the text it finds, and a label
# photo's prose (ingredients, marketing copy) is normally many times longer
# than the nutrition grid. That means the "best" variant/config for the
# *whole* image is really just the best one for the prose, and it's picked
# without ever checking whether it can still read the small gridded digits
# in the nutrition table. Often it can't. So the table gets its own pass:
# find it, crop tightly to it, and run its own variant/config contest scoped
# to just that region.

# Words that mark the start of the nutrition table, so its region can be
# located even when the table has no drawn border to detect.
_NUTRITION_ANCHOR_RE = re.compile(r"NUTRIT", re.IGNORECASE)


def _locate_nutrition_anchor(gray_image):
    """Finds the (top, left) pixel of the nutrition heading, if present."""

    try:
        data = pytesseract.image_to_data(
            gray_image, config="--oem 3 --psm 11", output_type=pytesseract.Output.DICT
        )
    except Exception:
        return None

    for i, text in enumerate(data.get("text", [])):
        if _NUTRITION_ANCHOR_RE.search(text or ""):
            return data["top"][i], data["left"][i]

    return None


def _find_bordered_box(gray_image, min_top):
    """
    Most FSSAI-style nutrition tables are drawn inside their own rounded
    rectangle. If one is visible below `min_top`, it's a far cleaner crop
    than any text-based guess — it excludes neighbouring columns (address
    blocks, batch codes) that sit beside the table on the same photo.
    Returns (x, y, w, h) or None if nothing box-shaped is found.
    """

    height, width = gray_image.shape[:2]

    edges = cv2.Canny(gray_image, 40, 120)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    best = None
    best_area = 0

    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)

        if y < max(0, min_top - int(height * 0.03)):
            continue
        if w < width * 0.3 or h < height * 0.08:
            continue

        area = w * h
        if area > best_area:
            best_area, best = area, (x, y, w, h)

    return best


# Nutrient names Indian nutrition tables almost always contain. Unlike the
# whole-image pass, this pass knows in advance roughly what it's looking
# for, so instead of scoring candidates by generic OCR confidence (which
# rewards the variant that reads the *most* text, not the variant that
# reads the *right* text), it scores them by how many of these expected
# labels actually come back recognisable. That matters because the
# generic-confidence winner for a dense grid can look "confident" while
# still mangling exactly the words the parser searches for afterwards
# (e.g. "Energy" -> "Erergy", "Sodium" -> "Sawin") — a fuzzy match against
# the label a candidate produces catches that a plain confidence score
# would miss.
_EXPECTED_NUTRIENT_WORDS = [
    "energy", "protein", "carbohydrate", "sugars", "fat",
    "cholesterol", "sodium",
]


def _keyword_recovery_score(text):
    """How many of the expected nutrient labels show up in this OCR text."""

    lowered = text.lower()
    hits = 0

    for word in _EXPECTED_NUTRIENT_WORDS:
        if word in lowered:
            hits += 1
            continue

        for line in lowered.split("\n"):
            line = line.strip()
            if len(line) < 3:
                continue
            if difflib.SequenceMatcher(None, word, line[: len(word) + 2]).ratio() > 0.72:
                hits += 1
                break

    return hits


def _gentle_clean(text):
    """
    Cleanup for the nutrition block only. clean_ocr_text drops any line
    that's mostly digits (letters/len < 0.35) on the assumption that it's
    a barcode or border — but a nutrition table row IS mostly digits once
    the label and its three columns are on one line, so that filter would
    delete exactly the content this pass exists to recover. This only
    strips noise characters and blank lines.
    """

    text = text.replace("\r", "\n")
    text = re.sub(NOISE_CHARS, " ", text)

    lines = []
    for line in text.split("\n"):
        line = re.sub(r"\s+", " ", line).strip()
        if line and len(line) >= 2:
            lines.append(line)

    return "\n".join(lines)


def extract_nutrition_block(image_path):
    """
    Locates the nutrition table, crops tightly to it, and re-OCRs just
    that region with settings suited to small gridded text. Returns the
    reconstructed table text, or "" if no table could be found — callers
    should treat that as "nothing extra to add", not an error.
    """

    image = cv2.imread(image_path)
    if image is None:
        return ""

    height, width = image.shape[:2]
    if width < 1800:
        scale = 1800 / width
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    elif width > 3200:
        scale = 3200 / width
        image = cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    img_h, img_w = gray.shape[:2]

    anchor = _locate_nutrition_anchor(gray)

    if anchor is None:
        # No "NUTRITION..." heading found at all — nothing to do here.
        return ""

    anchor_top, anchor_left = anchor

    box = _find_bordered_box(gray, anchor_top)

    if box is not None:
        x, y, w, h = box
        pad = int(min(w, h) * 0.02)
        x = max(0, x - pad)
        y = max(0, y - pad)
        w = min(img_w - x, w + 2 * pad)
        h = min(img_h - y, h + 2 * pad)
    else:
        # No visible border — fall back to "from the heading to the
        # bottom of the photo", which is coarser (may pick up a
        # neighbouring column of text) but still far better than letting
        # the table compete against the prose for the whole-image vote.
        x = 0
        y = max(0, anchor_top - int(img_h * 0.02))
        w = img_w
        h = img_h - y

    if w < 20 or h < 20:
        return ""

    crop = gray[y:y + h, x:x + w]

    # Table digits are small relative to the prose — upscaling here (on
    # top of the whole-image resize already applied) noticeably helps
    # Tesseract place decimal points and adjacent-column numbers correctly.
    crop = cv2.resize(crop, None, fx=1.8, fy=1.8, interpolation=cv2.INTER_CUBIC)

    clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
    balanced = clahe.apply(crop)

    _, otsu = cv2.threshold(balanced, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

    sharpen_kernel = np.array([[0, -1, 0], [-1, 5, -1], [0, -1, 0]])
    sharpened = cv2.filter2D(balanced, -1, sharpen_kernel)

    variants = [("balanced", balanced), ("otsu", otsu), ("sharpened", sharpened)]
    # psm 6 (one uniform block) keeps a table row's label and its columns
    # of numbers together on one line; psm 4 tries to detect column
    # boundaries itself and, on a tight numeric grid, routinely loses the
    # row structure entirely. It's kept as a fallback candidate only in
    # case psm 6 fails outright on a given photo.
    configs = ["--oem 3 --psm 6", "--oem 3 --psm 4"]

    candidates = []  # (keyword_score, confidence, text)

    for _, variant_image in variants:
        for config in configs:
            confidence, _ = score_variant(variant_image, config)
            candidate_text = pytesseract.image_to_string(variant_image, config=config)
            keyword_score = _keyword_recovery_score(candidate_text)

            if candidate_text.strip():
                candidates.append((keyword_score, confidence, candidate_text))

    if not candidates:
        return ""

    # No single preprocessing pass reads every row correctly — one variant
    # gets "Sodium" as a legible word but garbles its digits, another does
    # the opposite. Rather than betting everything on one "winner", every
    # candidate that found *something* is kept, best-first. parser.py's
    # nutrient regexes take the first match they find for each nutrient,
    # so stacking candidates this way means a nutrient only falls through
    # to a lower-quality candidate when every better one missed it
    # entirely — it can't make an already-correct reading worse.
    candidates.sort(key=lambda c: (c[0], c[1]), reverse=True)

    combined = "\n".join(_gentle_clean(text) for _, _, text in candidates if text.strip())
    return combined


# ============================================================
# MAIN ENTRY POINT
# ============================================================

def extract_text(image_path):
    """Runs several preprocessing variants and keeps the most confident read."""

    variants = preprocess_variants(image_path)

    # psm 6 = one uniform block, psm 4 = columns of variable-width text.
    configs = ["--oem 3 --psm 6", "--oem 3 --psm 4"]

    best_score = 0.0
    best_image = None
    best_config = configs[0]

    for _, image in variants:
        for config in configs:
            score, _ = score_variant(image, config)
            if score > best_score:
                best_score, best_image, best_config = score, image, config

    if best_image is None:
        raise ValueError("No readable text was found in this image.")

    text = pytesseract.image_to_string(best_image, config=best_config)
    text = clean_ocr_text(text)

    # Dedicated second pass so the nutrition table isn't left to compete
    # with the (usually much longer) prose for the single whole-image
    # variant choice above. Best-effort: if it can't find or read a table,
    # the whole-image text above is returned exactly as before.
    try:
        table_text = extract_nutrition_block(image_path)
    except Exception:
        table_text = ""

    if table_text.strip():
        text = f"{text}\n{table_text}" if text.strip() else table_text

    if not text.strip():
        raise ValueError("No readable text was found in this image.")

    return text
