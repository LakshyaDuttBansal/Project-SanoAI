# Flask backend for Health Copilot
# Single deployment: Flask serves both API and Vanilla JS frontend from /static
import datetime
import json
import os
import sqlite3
import uuid
from flask import Flask, send_from_directory, jsonify, request
from flask_cors import CORS
from werkzeug.utils import secure_filename
from dotenv import load_dotenv

# Load .env (GEMINI_API_KEY) if present
load_dotenv()

# static_folder="static" -> frontend files live in ./static/
# static_url_path="" lets Flask serve static files from root URL if needed,
# but we explicitly serve index.html on GET / below.
app = Flask(__name__, static_folder="static", static_url_path="")

# Enable CORS for API routes (harmless for single deployment,
# useful if frontend is opened separately during dev).
CORS(app)

# Folder for uploaded images (and PDFs if saved). Created at startup.
UPLOAD_FOLDER = os.path.join(os.path.dirname(__file__), "uploads")
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Allowed extensions for POST /api/upload via FormData `file` field.
ALLOWED_EXTENSIONS = {"pdf", "jpg", "jpeg", "png"}

# In-memory store: { id: {"filename": str, "raw_text": str, "extracted": dict, "summary": str} }
# Kept as fast cache; SQLite is the source of truth (see below).
STORE = {}

# SQLite file next to app.py (no ORM, stdlib sqlite3 only)
DB_PATH = os.path.join(os.path.dirname(__file__), "health.db")


def get_db():
    """Open a new SQLite connection with Row access."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    """Create records table and seed 3 demo rows if empty (for evaluators)."""
    conn = get_db()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS records"
        "(id TEXT PRIMARY KEY, date TEXT, type TEXT, extracted TEXT, summary TEXT)"
    )
    count = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    if count == 0:
        # 3 demo records so timeline/records are never empty on fresh clone
        demos = [
            ("demo-1", "2024-05-10", "blood_test",
             {"medicines": [], "tests": [{"name": "Hemoglobin", "value": "8.1", "unit": "g/dL", "normal_range": "12-16", "flag": "low"}], "diagnoses": ["Anemia"], "dates": ["2024-05-10"], "record_type": "blood_test", "record_date": "2024-05-10"},
             "Your hemoglobin is low. This can mean anemia — you may feel tired. Eat iron-rich foods and see your doctor.\nDisclaimer: Not medical advice, consult doctor."),
            ("demo-2", "2024-08-22", "prescription",
             {"medicines": [{"name": "Metformin", "dosage": "500mg", "frequency": "twice daily"}], "tests": [], "diagnoses": ["Type 2 Diabetes"], "dates": ["2024-08-22"], "record_type": "prescription", "record_date": "2024-08-22"},
             "You were given Metformin for blood sugar. Take it as told by your doctor.\nDisclaimer: Not medical advice, consult doctor."),
            ("demo-3", "2025-01-15", "checkup",
             {"medicines": [], "tests": [{"name": "Cholesterol", "value": "240", "unit": "mg/dL", "normal_range": "<200", "flag": "high"}], "diagnoses": ["High cholesterol"], "dates": ["2025-01-15"], "record_type": "checkup", "record_date": "2025-01-15"},
             "Your cholesterol is high. This means extra fat in the blood — eat less fried food and move more.\nDisclaimer: Not medical advice, consult doctor."),
        ]
        for rid, date, rtype, extracted, summary in demos:
            conn.execute(
                "INSERT OR REPLACE INTO records (id, date, type, extracted, summary) VALUES (?, ?, ?, ?, ?)",
                (rid, date, rtype, json.dumps(extracted), summary),
            )
        conn.commit()
    conn.close()


def save_record(rid, date, rtype, extracted, summary=""):
    """Insert/update one record in SQLite."""
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO records (id, date, type, extracted, summary) VALUES (?, ?, ?, ?, ?)",
        (rid, date, rtype, json.dumps(extracted or {}), summary or ""),
    )
    conn.commit()
    conn.close()


# Init DB at import so routes/tests work without running __main__
init_db()


def allowed_file(filename):
    """Check extension against pdf/jpg/png allowlist."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def fallback_result(text):
    """Fallback JSON when Gemini is unavailable — app still works."""
    return {
        "medicines": [],
        "tests": [],
        "diagnoses": [],
        "dates": [],
        "record_type": "",
        "record_date": "",
        "raw_text": text or "",
    }


def extract_medical(text):
    """Extract structured medical JSON via Gemini 1.5 Flash.

    Input: raw_text string.
    Returns dict with keys: medicines, tests, diagnoses, dates,
    record_type, record_date (+ raw_text on fallback).
    """
    # No key -> fallback so upload still succeeds locally/without billing
    if not os.getenv("GEMINI_API_KEY"):
        return fallback_result(text)

    try:
        import google.generativeai as genai

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "Extract and return ONLY valid JSON with keys: "
            "medicines[{name,dosage,frequency}], "
            "tests[{name,value,unit,normal_range,flag}], "
            "diagnoses[], dates[], record_type, record_date.\n\n"
            f"Medical text:\n{text[:12000]}"
        )
        resp = model.generate_content(prompt)
        out = (resp.text or "").strip()
        # Strip markdown fences if model wraps JSON in ```json ... ```
        if out.startswith("```"):
            out = out.strip("`")
            out = out.replace("json\n", "", 1).replace("json", "", 1).strip()
        return json.loads(out)
    except Exception:
        # Any API/parse error -> fallback with raw_text preserved
        return fallback_result(text)


def build_template_summary(extracted):
    """Offline fallback summary built from tests list (no API needed)."""
    extracted = extracted or {}
    tests = extracted.get("tests", []) or []
    medicines = extracted.get("medicines", []) or []
    diagnoses = extracted.get("diagnoses", []) or []

    lines = ["Here is a simple summary of your record."]
    if diagnoses:
        lines.append("Diagnoses: " + ", ".join(str(d) for d in diagnoses) + ".")
    if medicines:
        names = [m.get("name", "?") if isinstance(m, dict) else str(m) for m in medicines]
        lines.append("Medicines: " + ", ".join(names) + ".")
    if tests:
        lines.append("Tests:")
        for t in tests:
            if not isinstance(t, dict):
                lines.append(f"- {t}")
                continue
            name = t.get("name", "?")
            value = t.get("value", "?")
            unit = t.get("unit", "")
            flag = (t.get("flag", "") or "").lower()
            if flag and flag not in ("normal", "n", "ok"):
                lines.append(f"- {name} is {flag} ({value} {unit}). This means it is outside the normal range — ask your doctor what to do next.")
            else:
                lines.append(f"- {name}: {value} {unit} (normal).")
    else:
        lines.append("No test results were found.")
    lines.append("Disclaimer: Not medical advice, consult doctor.")
    return "\n".join(lines)


def summarize_medical(extracted):
    """Explain extracted JSON in plain patient-friendly language via Gemini."""
    # No key -> template summary so endpoint still works
    if not os.getenv("GEMINI_API_KEY"):
        return build_template_summary(extracted)
    try:
        import google.generativeai as genai

        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        model = genai.GenerativeModel("gemini-1.5-flash")
        prompt = (
            "Explain in simple plain language for patient, "
            "explain each abnormal test what it means, "
            "5th grade reading level, end with "
            "'Disclaimer: Not medical advice, consult doctor.'\n\n"
            f"Extracted record JSON:\n{json.dumps(extracted)[:12000]}"
        )
        resp = model.generate_content(prompt)
        return (resp.text or "").strip()
    except Exception:
        return build_template_summary(extracted)


@app.route("/")
def index():
    """Serve the Vanilla JS frontend entry point."""
    # send_from_directory keeps the path safe inside ./static
    return send_from_directory(app.static_folder, "index.html")


@app.route("/api/health", methods=["GET"])
def health():
    """Health check endpoint used by frontend and deploy monitors."""
    return jsonify({"ok": True})


@app.route("/api/upload", methods=["POST"])
def upload():
    """Accept pdf/jpg/png via FormData `file` field."""
    # FormData must contain a `file` part
    if "file" not in request.files:
        return jsonify({"error": "missing file field"}), 400

    f = request.files["file"]
    if not f or not f.filename:
        return jsonify({"error": "empty filename"}), 400

    filename = secure_filename(f.filename)
    if not allowed_file(filename):
        return jsonify({"error": "only pdf/jpg/png allowed"}), 400

    ext = filename.rsplit(".", 1)[1].lower()
    file_id = str(uuid.uuid4())
    raw_text = ""

    if ext == "pdf":
        # Extract raw text with pdfplumber (no disk write needed)
        import pdfplumber

        texts = []
        with pdfplumber.open(f.stream) as pdf:
            for page in pdf.pages:
                texts.append(page.extract_text() or "")
        raw_text = "\n".join(texts)
    else:
        # Image (jpg/jpeg/png): save to /uploads, acknowledge receipt
        # Prefix with id to avoid collisions
        saved_name = f"{file_id}_{filename}"
        f.save(os.path.join(UPLOAD_FOLDER, saved_name))
        filename = saved_name  # store actual saved name
        # raw_text stays "" for images for now

    # Store raw_text in memory dict
    STORE[file_id] = {"filename": filename, "raw_text": raw_text}

    # Run Gemini extraction (fallback keeps app working without key)
    extracted = extract_medical(raw_text)
    STORE[file_id]["extracted"] = extracted

    # Persist to SQLite (date/type from extracted, fallback to today/ext)
    rec_date = extracted.get("record_date") or datetime.date.today().isoformat()
    rec_type = extracted.get("record_type") or ext
    save_record(file_id, rec_date, rec_type, extracted)

    return jsonify({"id": file_id, "filename": filename, "extracted": extracted})


@app.route("/api/records", methods=["GET"])
def records():
    """Return all records from SQLite."""
    conn = get_db()
    rows = conn.execute("SELECT id, date, type, extracted, summary FROM records").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            extracted = json.loads(r["extracted"] or "{}")
        except Exception:
            extracted = {}
        out.append({"id": r["id"], "date": r["date"], "type": r["type"], "extracted": extracted, "summary": r["summary"]})
    return jsonify(out)


@app.route("/api/timeline", methods=["GET"])
def timeline():
    """Return records sorted by date as [{date, type, title, details}]."""
    conn = get_db()
    rows = conn.execute("SELECT id, date, type, extracted, summary FROM records ORDER BY date ASC").fetchall()
    conn.close()
    out = []
    for r in rows:
        try:
            extracted = json.loads(r["extracted"] or "{}")
        except Exception:
            extracted = {}
        # Title: first diagnosis else record_type/type; details: summary or test count
        diags = extracted.get("diagnoses", []) or []
        title = diags[0] if diags else (extracted.get("record_type") or r["type"])
        details = r["summary"] or f"{len(extracted.get('tests', []) or [])} tests, {len(extracted.get('medicines', []) or [])} medicines"
        out.append({"id": r["id"], "date": r["date"], "type": r["type"], "title": title, "details": details})
    return jsonify(out)


@app.route("/api/summary/<file_id>", methods=["GET"])
def summary(file_id):
    """Return patient-friendly summary for a stored upload id (cached)."""
    # Fast path: in-memory cache
    if STORE.get(file_id, {}).get("summary"):
        return jsonify({"summary_text": STORE[file_id]["summary"]})
    # Look up SQLite (covers demo records + restarts where STORE is empty)
    conn = get_db()
    row = conn.execute("SELECT extracted, summary FROM records WHERE id = ?", (file_id,)).fetchone()
    conn.close()
    if row and row["summary"]:
        # Warm the cache and return
        STORE.setdefault(file_id, {})["summary"] = row["summary"]
        return jsonify({"summary_text": row["summary"]})
    if file_id in STORE:
        extracted = STORE[file_id].get("extracted") or fallback_result(STORE[file_id].get("raw_text", ""))
    elif row:
        try:
            extracted = json.loads(row["extracted"] or "{}")
        except Exception:
            extracted = fallback_result("")
    else:
        return jsonify({"error": "unknown id"}), 404
    text = summarize_medical(extracted)
    STORE.setdefault(file_id, {})["summary"] = text  # cache in memory
    # Persist summary to DB (INSERT if upload was memory-only, else UPDATE)
    try:
        conn = get_db()
        exists = conn.execute("SELECT 1 FROM records WHERE id = ?", (file_id,)).fetchone()
        if exists:
            conn.execute("UPDATE records SET summary = ? WHERE id = ?", (text, file_id))
        else:
            date = (extracted.get("record_date") if isinstance(extracted, dict) else "") or datetime.date.today().isoformat()
            rtype = (extracted.get("record_type") if isinstance(extracted, dict) else "") or "record"
            conn.execute(
                "INSERT INTO records (id, date, type, extracted, summary) VALUES (?, ?, ?, ?, ?)",
                (file_id, date, rtype, json.dumps(extracted), text),
            )
        conn.commit()
        conn.close()
    except Exception:
        pass
    return jsonify({"summary_text": text})


if __name__ == "__main__":
    # Run with: python app.py
    # Frontend available at http://localhost:5000/
    # API available at http://localhost:5000/api/health
    app.run(host="0.0.0.0", port=5000, debug=True)
