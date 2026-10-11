# SANO db layer - sqlite3 stdlib only, no ORM
import os
import sqlite3
import uuid
import json
import datetime

BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "sano.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""CREATE TABLE IF NOT EXISTS records(
        id TEXT PRIMARY KEY, title TEXT, type TEXT, date TEXT,
        status TEXT, extracted TEXT, summary TEXT, filename TEXT)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS medications(
        id TEXT PRIMARY KEY, name TEXT, dose TEXT, schedule TEXT,
        status TEXT DEFAULT 'current', adherence INTEGER DEFAULT 100)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS wellness(
        date TEXT PRIMARY KEY, steps INT DEFAULT 0, sleep_hrs FLOAT DEFAULT 0,
        water_l FLOAT DEFAULT 0, heart_rate INT DEFAULT 0)""")
    conn.execute("""CREATE TABLE IF NOT EXISTS appointments(
        id TEXT PRIMARY KEY, title TEXT, date TEXT, status TEXT DEFAULT 'upcoming')""")
    conn.execute("""CREATE TABLE IF NOT EXISTS users(
        id TEXT PRIMARY KEY, name TEXT, email TEXT UNIQUE, pw_hash TEXT, token TEXT)""")
    # Demo test login for evaluators (Test Access Notes)
    import hashlib
    demo_hash = hashlib.sha256("test12345".encode()).hexdigest()
    if conn.execute("SELECT COUNT(*) FROM users WHERE email='test@sano.ai'").fetchone()[0] == 0:
        conn.execute("INSERT INTO users VALUES(?,?,?,?,?)",
                     (str(uuid.uuid4()), "Test User", "test@sano.ai", demo_hash, ""))

    # Seed generic demo only if empty (ignore sample John data from UI image)
    if conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] == 0:
        demos = [
            ("demo-1", "Complete Blood Count (CBC)", "lab_report", "2025-05-12", "Normal",
             {"medicines": [], "tests": [{"name": "Hemoglobin", "value": "13.8", "unit": "g/dL",
              "normal_range": "12-16", "flag": "Normal"}], "diagnoses": [], "dates": ["2025-05-12"]},
             "CBC is normal.", "CBC Report - 12 May 2025.pdf"),
            ("demo-2", "Prescription - Blood Pressure", "prescription", "2025-04-10", "Active",
             {"medicines": [{"name": "Amlodipine", "dosage": "5mg", "frequency": "once daily"}],
              "tests": [], "diagnoses": ["Hypertension"], "dates": ["2025-04-10"]},
             "Blood pressure prescription.", "rx.pdf"),
        ]
        for rid, title, rtype, date, status, ext, summ, fn in demos:
            conn.execute("INSERT OR REPLACE INTO records VALUES(?,?,?,?,?,?,?,?)",
                         (rid, title, rtype, date, status, json.dumps(ext), summ, fn))
    if conn.execute("SELECT COUNT(*) FROM medications").fetchone()[0] == 0:
        for name, dose, sched in [("Amlodipine", "5mg", "8:00 AM"),
                                  ("Metformin", "500mg", "8:00 AM, 8:00 PM"),
                                  ("Vitamin D3", "1000 IU", "9:00 AM")]:
            conn.execute("INSERT INTO medications VALUES(?,?,?,?,?,?)",
                         (str(uuid.uuid4()), name, dose, sched, "current", 92))
    if conn.execute("SELECT COUNT(*) FROM wellness").fetchone()[0] == 0:
        base = datetime.date(2025, 5, 6)
        for i, (st, sl, wa, hr) in enumerate([(6842, 7.5, 2.1, 72), (7000, 7.0, 2.0, 74),
                                              (8240, 8.0, 2.2, 70), (6000, 6.5, 1.8, 76)]):
            d = (base + datetime.timedelta(days=i)).isoformat()
            conn.execute("INSERT OR REPLACE INTO wellness VALUES(?,?,?,?,?)", (d, st, sl, wa, hr))
    if conn.execute("SELECT COUNT(*) FROM appointments").fetchone()[0] == 0:
        conn.execute("INSERT INTO appointments VALUES(?,?,?,?)",
                     (str(uuid.uuid4()), "General Checkup", "2025-05-16", "upcoming"))
        conn.execute("INSERT INTO appointments VALUES(?,?,?,?)",
                     (str(uuid.uuid4()), "Lipid Profile Review", "2025-05-28", "upcoming"))
    conn.commit()
    conn.close()
