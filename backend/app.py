import os
import uuid
from datetime import datetime, timezone

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient, DESCENDING
from pymongo.errors import DuplicateKeyError
from werkzeug.security import check_password_hash, generate_password_hash

import technical


load_dotenv()

from enrich import AI_ENABLED, enrich_label
from ocr import extract_text
from parser import parse_label
from simplifier import (
    build_flags,
    calculate_food_score,
    simplify_ingredients,
    simplify_nutrition,
)

# ============================================================
# CONFIGURATION
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(BASE_DIR)

FRONTEND_DIR = os.path.join(PROJECT_DIR, "frontend")
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")

# Local dev: point this at your local MongoDB install (mongod running on the
# default port). On Render: create a MongoDB Atlas cluster (Render doesn't
# host Mongo itself) and set MONGODB_URI to its connection string as an
# environment variable — this line then picks it up automatically.
MONGODB_URI = os.environ.get("MONGODB_URI", "mongodb://localhost:27017")
MONGODB_DB_NAME = os.environ.get("MONGODB_DB_NAME", "foodlens")

os.makedirs(UPLOAD_FOLDER, exist_ok=True)

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 12 * 1024 * 1024  # 12 MB
CORS(app)


# ============================================================
# DATABASE  (MongoDB — user creds + saved scan results only)
# ============================================================

mongo_client = MongoClient(MONGODB_URI, tz_aware=True)
db = mongo_client[MONGODB_DB_NAME]
users_col = db["users"]
results_col = db["results"]


def init_db():
    # Enforces one account per email at the database level.
    users_col.create_index("email", unique=True)
    # Every history lookup filters by user_id, then sorts by newest first.
    results_col.create_index([("user_id", 1), ("created_at", DESCENDING)])


init_db()


def to_object_id(raw_id):
    """Returns a bson ObjectId for a request-supplied id, or None if invalid."""
    try:
        return ObjectId(str(raw_id))
    except (InvalidId, TypeError):
        return None


# ============================================================
# FRONTEND
# ============================================================

@app.route("/")
def home():
    return send_from_directory(FRONTEND_DIR, "login.html")


@app.route("/<path:path>")
def frontend_files(path):
    return send_from_directory(FRONTEND_DIR, path)


# ============================================================
# AUTH
# ============================================================

@app.route("/api/register", methods=["POST"])
def register():
    data = request.get_json(silent=True) or {}

    name = data.get("name", "").strip()
    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not name or not email or not password:
        return jsonify({"success": False, "message": "Fill in every field to continue."}), 400

    if len(password) < 6:
        return jsonify({"success": False, "message": "Use at least 6 characters for the password."}), 400

    try:
        users_col.insert_one(
            {"name": name, "email": email, "password": generate_password_hash(password)}
        )
    except DuplicateKeyError:
        return jsonify({"success": False, "message": "That email is already registered."}), 409

    return jsonify({"success": True, "message": "Account created."})


@app.route("/api/login", methods=["POST"])
def login():
    data = request.get_json(silent=True) or {}

    email = data.get("email", "").strip().lower()
    password = data.get("password", "")

    if not email or not password:
        return jsonify({"success": False, "message": "Enter your email and password."}), 400

    user = users_col.find_one({"email": email})

    if not user or not check_password_hash(user["password"], password):
        return jsonify({"success": False, "message": "That email and password don't match."}), 401

    return jsonify(
        {
            "success": True,
            "user": {"id": str(user["_id"]), "name": user["name"], "email": user["email"]},
        }
    )


# ============================================================
# SAVED RESULTS  (per-user scan history)
# ============================================================

@app.route("/api/results", methods=["POST"])
def save_result():
    body = request.get_json(silent=True) or {}

    user_id = to_object_id(body.get("user_id"))
    data = body.get("data")

    if not user_id or not isinstance(data, dict):
        return jsonify({"success": False, "message": "Missing user_id or data."}), 400

    name = (body.get("name") or "").strip() or "Scanned label"
    score = body.get("score")
    scored = isinstance(score, (int, float))
    score_label = body.get("score_label") or "Not scored"
    flags_count = int(body.get("flags_count") or 0)
    thumb = body.get("thumb") or ""
    source = body.get("source") if body.get("source") in ("upload", "camera") else "upload"
    created_at = datetime.now(timezone.utc)

    result = results_col.insert_one(
        {
            "user_id": user_id,
            "name": name,
            "score": score if scored else None,
            "scored": scored,
            "score_label": score_label,
            "flags_count": flags_count,
            "thumb": thumb,
            "source": source,
            "data": data,
            "created_at": created_at,
        }
    )

    return jsonify(
        {"success": True, "id": str(result.inserted_id), "ts": created_at.timestamp() * 1000}
    )


@app.route("/api/results/<user_id>", methods=["GET"])
def get_results(user_id):
    object_id = to_object_id(user_id)
    if not object_id:
        return jsonify({"success": False, "message": "Invalid user id."}), 400

    docs = results_col.find({"user_id": object_id}).sort("created_at", DESCENDING)
    entries = [
        {
            "id": str(doc["_id"]),
            "ts": doc["created_at"].timestamp() * 1000,
            "name": doc["name"],
            "score": doc["score"] if doc["scored"] else 0,
            "scored": doc["scored"],
            "scoreLabel": doc["score_label"],
            "flagsCount": doc["flags_count"],
            "thumb": doc["thumb"],
            # Older entries saved before this field existed default to
            # "upload" so they still land in a sensible bucket.
            "source": doc.get("source", "upload"),
            "data": doc["data"],
        }
        for doc in docs
    ]

    return jsonify({"success": True, "entries": entries})


@app.route("/api/results/<result_id>", methods=["PATCH"])
def rename_result(result_id):
    """Renames a saved scan. Only the owner (user_id in the body) can rename it."""
    body = request.get_json(silent=True) or {}

    object_id = to_object_id(result_id)
    user_id = to_object_id(body.get("user_id"))
    if not object_id or not user_id:
        return jsonify({"success": False, "message": "Invalid scan or user id."}), 400

    name = " ".join(str(body.get("name") or "").split())[:80]
    if not name:
        return jsonify({"success": False, "message": "Name can't be empty."}), 400

    res = results_col.update_one({"_id": object_id, "user_id": user_id}, {"$set": {"name": name}})
    if res.matched_count == 0:
        return jsonify({"success": False, "message": "Scan not found."}), 404

    return jsonify({"success": True, "name": name})


# ============================================================
# ANALYSIS PIPELINE
# ============================================================

def analyze_label(raw_text):
    """
    OCR text in, finished result out.

        parse (regex)  ──▶  AI pass (cleans + explains)  ──▶  cards
                               └─ unavailable ──▶ offline table + rules
    """

    parsed = parse_label(raw_text)

    enriched = enrich_label(raw_text)

    if enriched["available"]:
        items = enriched["items"]
        flags = build_flags(items)
        product_guess = enriched["product_guess"]
        mode = enriched["mode"]
        note = ""

        # Regex readings are grounded in the literal text, so they win.
        # The AI reading fills gaps where the table was too mangled to match.
        nutrition_dict = dict(enriched["nutrition"])
        nutrition_dict.update(parsed["nutrition"])
    else:
        offline = simplify_ingredients(parsed["ingredients"])
        items = offline["items"]
        flags = offline["flags"]
        nutrition_dict = parsed["nutrition"]
        product_guess = ""
        mode = "offline"
        note = enriched["note"]

    # The chemistry layer runs on whichever set of cards came back, so an
    # AI card and an offline card carry the same technical block.
    technical.attach_technical(items)
    micronutrients = technical.extract_micronutrients(raw_text, items)

    nutrition = simplify_nutrition(nutrition_dict)
    score = calculate_food_score(nutrition_dict, items)

    return {
        "success": True,
        "raw_text": raw_text,
        "product_guess": product_guess,
        "ingredients": items,
        "allergens": flags["allergens"],
        "additives": flags["additives"],
        "sweeteners": flags["sweeteners"],
        "nutrition": nutrition["items"],
        "nutrition_summary": nutrition["summary"],
        "vitamins": micronutrients["items"],
        "vitamins_unnamed": micronutrients["declared_unnamed"],
        "score": score["score"],
        "score_label": score["label"],
        "score_reasons": score["reasons"],
        "score_note": score["note"],
        "analysis_mode": mode,
        "analysis_note": note,
    }


# ============================================================
# IMAGE ANALYSIS
# ============================================================

@app.route("/api/analyze-image", methods=["POST"])
def analyze_image():
    if "image" not in request.files:
        return jsonify({"success": False, "message": "No image came through."}), 400

    file = request.files["image"]

    if not file.filename:
        return jsonify({"success": False, "message": "Pick an image first."}), 400

    extension = os.path.splitext(file.filename)[1].lower()

    if extension not in [".jpg", ".jpeg", ".png", ".webp"]:
        extension = ".jpg"

    path = os.path.join(UPLOAD_FOLDER, f"{uuid.uuid4().hex}{extension}")

    try:
        file.save(path)

        raw_text = extract_text(path)

        if not raw_text.strip():
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "No text came off that photo. Move closer to the panel and keep it flat.",
                    }
                ),
                422,
            )

        result = analyze_label(raw_text)

        if not result["ingredients"] and not result["nutrition"]:
            return (
                jsonify(
                    {
                        "success": False,
                        "message": "Text was readable but no ingredient list or nutrition table was found. Try the back-of-pack panel.",
                    }
                ),
                422,
            )

        return jsonify(result)

    except Exception as error:
        print("ANALYSIS ERROR:", error)
        return jsonify({"success": False, "message": f"Analysis failed: {error}"}), 500

    finally:
        if os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


# ============================================================
# HEALTH CHECK
# ============================================================

@app.route("/api/status")
def status():
    return jsonify({"ok": True, "ai_enabled": AI_ENABLED})


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":
    # Render (and most hosts) inject PORT; fall back to 5000 for local dev.
    # In production, Render actually runs this app via gunicorn (see the
    # Procfile), so this block only ever executes when you run
    # `python app.py` yourself.
    port = int(os.environ.get("PORT", 5000))
    is_local = "PORT" not in os.environ

    print("\n====================================")
    print("        FOODLENS SERVER")
    print("====================================")
    print("Ingredient explanations:", "AI (dynamic)" if AI_ENABLED else "offline table")
    print(f"Open: http://127.0.0.1:{port}" if is_local else f"Listening on port {port}")
    print("====================================\n")

    app.run(host="127.0.0.1" if is_local else "0.0.0.0", port=port, debug=is_local)