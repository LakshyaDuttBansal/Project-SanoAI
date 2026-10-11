# SANO - Personal Health Copilot (FastAPI backend)
import os
import json
import uuid
import datetime
from fastapi import FastAPI, UploadFile, File, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from dotenv import load_dotenv
from db import get_db, init_db
from ai import extract_medical, summarize_medical, chat_reply, classify_status
from models import ChatIn, MedicationIn, AppointmentIn, SignupIn, LoginIn

load_dotenv()
init_db()

app = FastAPI(title="SANO")
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)
ALLOWED = {"pdf", "jpg", "jpeg", "png"}


@app.get("/api/health")
def health():
    return {"ok": True, "project": "SANO"}


@app.post("/api/upload")
async def upload(file: UploadFile = File(...)):
    ext = (file.filename or "").rsplit(".", 1)[-1].lower() if "." in (file.filename or "") else ""
    if ext not in ALLOWED:
        raise HTTPException(status_code=400, detail="only pdf/jpg/png allowed")
    data = await file.read()
    raw_text, img_bytes = "", None
    if ext == "pdf":
        import pdfplumber, io
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            raw_text = "\n".join([(p.extract_text() or "") for p in pdf.pages])
    else:
        img_bytes = data
        # save image for record
        with open(os.path.join(UPLOAD_DIR, f"{uuid.uuid4()}_{file.filename}"), "wb") as f:
            f.write(data)
    extracted = extract_medical(raw_text, img_bytes)
    rid = str(uuid.uuid4())
    title = extracted.get("title") or file.filename
    rtype = extracted.get("type") or ("lab_report" if ext == "pdf" else "other")
    date = extracted.get("record_date") or datetime.date.today().isoformat()
    status = classify_status(extracted)
    summary = summarize_medical(extracted)
    conn = get_db()
    conn.execute("INSERT INTO records VALUES(?,?,?,?,?,?,?,?)",
                 (rid, title, rtype, date, status, json.dumps(extracted), summary, file.filename))
    conn.commit()
    conn.close()
    return {"id": rid, "title": title, "type": rtype, "date": date, "status": status, "extracted": extracted}


@app.get("/api/records")
def records(record_type: str = Query("all", alias="type"), search: str = "", sort: str = "latest"):
    conn = get_db()
    rows = conn.execute("SELECT * FROM records").fetchall()
    conn.close()
    out = []
    for r in rows:
        d = dict(r)
        d["extracted"] = json.loads(d["extracted"] or "{}")
        if record_type != "all" and d["type"] != record_type:
            continue
        if search and search.lower() not in (d["title"] or "").lower():
            continue
        out.append(d)
    out.sort(key=lambda x: x["date"], reverse=(sort == "latest"))
    return out


@app.get("/api/records/{rid}")
def record_one(rid: str):
    conn = get_db()
    r = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not r:
        return {"error": "not found"}
    d = dict(r)
    d["extracted"] = json.loads(d["extracted"] or "{}")
    return d


@app.get("/api/overview")
def overview():
    conn = get_db()
    total = conn.execute("SELECT COUNT(*) FROM records").fetchone()[0]
    meds = conn.execute("SELECT COUNT(*) FROM medications WHERE status='current'").fetchone()[0]
    appts = conn.execute("SELECT COUNT(*) FROM appointments WHERE status='upcoming'").fetchone()[0]
    w = conn.execute("SELECT AVG(sleep_hrs) FROM wellness").fetchone()[0] or 0
    # simple insights from abnormal tests
    recs = conn.execute("SELECT extracted FROM records").fetchall()
    conn.close()
    insights = []
    for (ext,) in recs:
        try:
            e = json.loads(ext or "{}")
            for t in e.get("tests", []) or []:
                if isinstance(t, dict) and (t.get("flag", "").lower() not in ("", "normal")):
                    insights.append({"title": f"{t.get('name')} {t.get('flag')}",
                                     "desc": f"{t.get('name')} {t.get('value')} {t.get('unit','')} (normal {t.get('normal_range','')}). Discuss with doctor."})
        except Exception:
            pass
    return {"total_records": total, "active_meds_count": meds, "upcoming_count": appts,
            "avg_sleep": round(w, 1), "insights": insights[:5]}


@app.get("/api/timeline")
def timeline():
    conn = get_db()
    rows = conn.execute("SELECT * FROM records ORDER BY date ASC").fetchall()
    conn.close()
    return [{"id": r["id"], "date": r["date"], "type": r["type"], "title": r["title"],
             "status": r["status"], "details": (r["summary"] or "")[:200]} for r in rows]


@app.get("/api/summary/{rid}")
def summary(rid: str):
    conn = get_db()
    r = conn.execute("SELECT * FROM records WHERE id=?", (rid,)).fetchone()
    conn.close()
    if not r:
        return {"error": "not found"}
    return {"summary_text": r["summary"]}


@app.post("/api/chat")
def chat(body: ChatIn):
    conn = get_db()
    rows = conn.execute("SELECT title, extracted FROM records").fetchall()
    conn.close()
    ctx = "; ".join([f"{x['title']}: {x['extracted'][:1000]}" for x in rows])
    reply = chat_reply(body.message, ctx)
    srcs = [x["title"] for x in rows][:3]
    return {"reply": reply, "sources": srcs}


@app.get("/api/medications")
def meds_list(status: str = "current"):
    conn = get_db()
    rows = conn.execute("SELECT * FROM medications WHERE status=?", (status,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/medications")
def meds_add(b: MedicationIn):
    mid = str(uuid.uuid4())
    conn = get_db()
    conn.execute("INSERT INTO medications VALUES(?,?,?,?,?,?)", (mid, b.name, b.dose, b.schedule, b.status, 100))
    conn.commit()
    row = conn.execute("SELECT * FROM medications WHERE id=?", (mid,)).fetchone()
    conn.close()
    return dict(row) if row else {"id": mid}


@app.delete("/api/medications/{mid}")
def meds_delete(mid: str):
    conn = get_db()
    conn.execute("DELETE FROM medications WHERE id=?", (mid,))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.get("/api/adherence")
def adherence(period: int = 30):
    # Period-aware demo values so 7d/30d selector stays coherent
    if period == 7:
        return {"percent": 86, "taken": 6, "missed": 1, "period": 7}
    return {"percent": 92, "taken": 28, "missed": 2, "period": 30}


@app.get("/api/wellness")
def wellness(range_param: str = Query("7d", alias="range")):
    conn = get_db()
    rows = conn.execute("SELECT * FROM wellness ORDER BY date").fetchall()
    conn.close()
    d = [dict(r) for r in rows]
    if not d:
        return {"series": [], "avg_steps": 0, "avg_sleep": 0, "avg_water": 0, "avg_hr": 0}
    return {"series": d, "avg_steps": sum(x["steps"] for x in d) // len(d),
            "avg_sleep": round(sum(x["sleep_hrs"] for x in d) / len(d), 1),
            "avg_water": round(sum(x["water_l"] for x in d) / len(d), 1),
            "avg_hr": sum(x["heart_rate"] for x in d) // len(d)}


@app.get("/api/appointments")
def appts():
    conn = get_db()
    rows = conn.execute("SELECT * FROM appointments ORDER BY date").fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.post("/api/appointments")
def appts_add(b: AppointmentIn):
    aid = str(uuid.uuid4())
    conn = get_db()
    conn.execute("INSERT INTO appointments VALUES(?,?,?,?)", (aid, b.title, b.date, b.status))
    conn.commit()
    conn.close()
    return {"id": aid}


@app.get("/")
def index():
    p = os.path.join(BASE_DIR, "static", "index.html")
    return FileResponse(p) if os.path.exists(p) else {"project": "SANO"}


@app.get("/login")
def login_page():
    p = os.path.join(BASE_DIR, "static", "login.html")
    return FileResponse(p) if os.path.exists(p) else {"project": "SANO"}


def _hash(pw: str) -> str:
    import hashlib
    return hashlib.sha256(pw.encode()).hexdigest()


@app.post("/api/auth/signup")
def signup(b: SignupIn):
    import secrets
    email = b.email.strip().lower()
    if len(b.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be 8+ characters")
    if not b.name.strip():
        raise HTTPException(status_code=400, detail="Name required")
    conn = get_db()
    if conn.execute("SELECT 1 FROM users WHERE email=?", (email,)).fetchone():
        conn.close()
        raise HTTPException(status_code=400, detail="Email already registered")
    uid, token = str(uuid.uuid4()), secrets.token_hex(24)
    conn.execute("INSERT INTO users VALUES(?,?,?,?,?)", (uid, b.name.strip(), email, _hash(b.password), token))
    conn.commit()
    conn.close()
    user = {"id": uid, "name": b.name.strip(), "email": email}
    # Dual shape: legacy {token,user} + new {access_token,user_id,name} for login.js
    return {"token": token, "access_token": token, "user": user,
            "user_id": uid, "name": user["name"], "email": email}


@app.post("/api/auth/login")
def login(b: LoginIn):
    import secrets
    email = b.email.strip().lower()
    conn = get_db()
    r = conn.execute("SELECT * FROM users WHERE email=?", (email,)).fetchone()
    if not r or r["pw_hash"] != _hash(b.password):
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid email or password")
    token = secrets.token_hex(24)
    conn.execute("UPDATE users SET token=? WHERE id=?", (token, r["id"]))
    conn.commit()
    conn.close()
    user = {"id": r["id"], "name": r["name"], "email": r["email"]}
    return {"token": token, "access_token": token, "user": user,
            "user_id": r["id"], "name": r["name"], "email": r["email"]}


if os.path.isdir(os.path.join(BASE_DIR, "static")):
    app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "static")), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
