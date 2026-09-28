# Running FoodLens locally in VS Code

This app is a single Flask server: `backend/app.py` serves both the API
*and* the `frontend/` HTML/CSS/JS files (as static files) on the same
port. There's no separate frontend server to run — you only ever start
the backend.

## 1. Open the folder in VS Code

Open the folder that contains `backend/` and `frontend/` as your
workspace root.

## 2. Create a virtual environment (recommended)

In the VS Code terminal, from the project root:

```bash
python -m venv backend/venv
```

Activate it:

- **Windows:** `backend\venv\Scripts\activate`
- **macOS/Linux:** `source backend/venv/bin/activate`

(In VS Code, you can also press `Ctrl+Shift+P` → "Python: Select
Interpreter" and point it at `backend/venv`.)

## 3. Install Python dependencies

```bash
pip install -r backend/requirements.txt
```

## 4. Install Tesseract OCR (needed for label scanning)

The `pytesseract` package is just a Python wrapper — you also need the
actual Tesseract binary installed on your system:

- **Windows:** install from https://github.com/UB-Mannheim/tesseract/wiki,
  then either add it to your PATH or set `TESSERACT_CMD` in `backend/.env`
  to the full path of `tesseract.exe`.
- **macOS:** `brew install tesseract`
- **Linux (Debian/Ubuntu):** `sudo apt install tesseract-ocr`

## 5. Set up MongoDB

The app needs a MongoDB instance for user accounts and saved scans.

- **Easiest for local dev:** install MongoDB Community Server
  (https://www.mongodb.com/try/download/community) and run `mongod` —
  the included `backend/.env` already points at
  `mongodb://localhost:27017`, so no changes needed.
- **Or use a free cloud cluster:** create one at
  https://www.mongodb.com/cloud/atlas, then replace `MONGODB_URI` in
  `backend/.env` with your Atlas connection string.

`backend/.env` is already filled in with local defaults — edit it if you
want to point at Atlas instead, or add an `ANTHROPIC_API_KEY` to enable
AI-written ingredient explanations (optional; the app works fine without
one, using its built-in offline ingredient tables).

## 6. Run the app

```bash
python backend/app.py
```

Then open **http://localhost:5000** in your browser (Flask's default
port — check the terminal output for the exact URL/port it prints).

## Notes

- `Procfile`, `render.yaml`, and `Dockerfile` from the original project
  are Render/Heroku deployment-only files — you don't need them to run
  locally in VS Code, so they've been left out of this copy.
- `backend/uploads/` is where scanned images are temporarily saved; it's
  included but empty.
- If you previously used the MongoDB Atlas credentials that shipped in
  this project's old `.env.example`, treat them as compromised (they
  were shared in a chat) — rotate that Atlas user's password before
  relying on that database for anything real.
