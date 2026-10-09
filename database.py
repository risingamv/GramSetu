"""
GramSetu (ग्राम सेतु) - SQLite Database Layer
Persistent storage for rural grievances, citizen records, AI classification routing, and resolution proofs.
"""

import sqlite3
import os
import json
import secrets
from datetime import datetime, timedelta

DB_PATH = os.path.join(os.path.dirname(__file__), "grievances.db")

OTP_EXPIRY_MINUTES = 5
SESSION_EXPIRY_DAYS = 7
OTP_RESEND_SECONDS = 60

def _hash_value(value):
    import hashlib
    return hashlib.sha256(value.encode("utf-8")).hexdigest()

def hash_otp(otp):
    import hmac
    import os
    secret = os.environ.get("GRAMSETU_OTP_SECRET", "dev-only-change-this-secret")
    return hmac.new(secret.encode("utf-8"), otp.encode("utf-8"), "sha256").hexdigest()

def normalize_phone(phone):
    digits = "".join(ch for ch in str(phone or "") if ch.isdigit())
    if digits.startswith("91") and len(digits) == 12:
        digits = digits[2:]
    return digits

def _cleanup_auth_records(cursor):
    now = datetime.now().isoformat()
    cursor.execute("DELETE FROM otp_codes WHERE expires_at < ? OR used = 1", (now,))
    cursor.execute("DELETE FROM sessions WHERE expires_at < ?", (now,))

def get_user_by_phone(phone):
    phone = normalize_phone(phone)
    conn = get_connection()
    row = conn.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row) if row else None

def upsert_user(phone, name="", village="", district="", role="citizen"):
    phone = normalize_phone(phone)
    conn = get_connection()
    cursor = conn.cursor()
    existing = cursor.execute("SELECT id FROM users WHERE phone = ?", (phone,)).fetchone()
    now = datetime.now().isoformat()
    if existing:
        cursor.execute("""
            UPDATE users SET
                name = COALESCE(NULLIF(?, ''), name),
                village = COALESCE(NULLIF(?, ''), village),
                district = COALESCE(NULLIF(?, ''), district),
                last_login_at = ?
            WHERE phone = ?
        """, (name, village, district, now, phone))
    else:
        cursor.execute("""
            INSERT INTO users (phone, name, village, district, role, created_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (phone, name, village, district, role, now, now))
    conn.commit()
    row = cursor.execute("SELECT * FROM users WHERE phone = ?", (phone,)).fetchone()
    conn.close()
    return dict(row)

def create_otp(phone, otp_hash):
    phone = normalize_phone(phone)
    conn = get_connection()
    cursor = conn.cursor()
    _cleanup_auth_records(cursor)
    now = datetime.now()
    recent = cursor.execute("""
        SELECT created_at FROM otp_codes
        WHERE phone = ? ORDER BY created_at DESC LIMIT 1
    """, (phone,)).fetchone()
    if recent:
        elapsed = (now - datetime.fromisoformat(recent["created_at"])).total_seconds()
        if elapsed < OTP_RESEND_SECONDS:
            conn.close()
            raise ValueError(f"Please wait {int(OTP_RESEND_SECONDS - elapsed)} seconds before requesting another OTP.")
    # Limit OTP requests to 5 per rolling hour.
    hour_ago = (now - timedelta(hours=1)).isoformat()
    count = cursor.execute(
        "SELECT COUNT(*) AS count FROM otp_codes WHERE phone = ? AND created_at >= ?",
        (phone, hour_ago)
    ).fetchone()["count"]
    if count >= 5:
        conn.close()
        raise ValueError("Too many OTP requests. Please try again later.")
    expires_at = (now + timedelta(minutes=OTP_EXPIRY_MINUTES)).isoformat()
    cursor.execute("""
        INSERT INTO otp_codes (phone, otp_hash, created_at, expires_at, attempts, used)
        VALUES (?, ?, ?, ?, 0, 0)
    """, (phone, otp_hash, now.isoformat(), expires_at))
    conn.commit()
    conn.close()
    return expires_at

def verify_otp(phone, otp_hash):
    phone = normalize_phone(phone)
    conn = get_connection()
    cursor = conn.cursor()
    _cleanup_auth_records(cursor)
    row = cursor.execute("""
        SELECT * FROM otp_codes
        WHERE phone = ? AND used = 0
        ORDER BY created_at DESC LIMIT 1
    """, (phone,)).fetchone()
    if not row:
        conn.close()
        return False, "No active OTP. Please request a new OTP."
    if row["attempts"] >= 5:
        cursor.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return False, "Too many incorrect attempts. Please request a new OTP."
    if row["expires_at"] < datetime.now().isoformat():
        cursor.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return False, "OTP expired. Please request a new OTP."
    if row["otp_hash"] != otp_hash:
        cursor.execute("UPDATE otp_codes SET attempts = attempts + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        conn.close()
        return False, "Invalid OTP."
    cursor.execute("UPDATE otp_codes SET used = 1 WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True, "OTP verified."

def create_session(phone):
    phone = normalize_phone(phone)
    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_value(raw_token)
    now = datetime.now()
    expires_at = (now + timedelta(days=SESSION_EXPIRY_DAYS)).isoformat()
    conn = get_connection()
    cursor = conn.cursor()
    _cleanup_auth_records(cursor)
    cursor.execute("""
        INSERT INTO sessions (token_hash, phone, created_at, expires_at)
        VALUES (?, ?, ?, ?)
    """, (token_hash, phone, now.isoformat(), expires_at))
    conn.commit()
    conn.close()
    return raw_token

def get_user_by_session(raw_token):
    if not raw_token:
        return None
    token_hash = _hash_value(raw_token)
    conn = get_connection()
    row = conn.execute("""
        SELECT u.* FROM sessions s
        JOIN users u ON u.phone = s.phone
        WHERE s.token_hash = ? AND s.expires_at > ?
    """, (token_hash, datetime.now().isoformat())).fetchone()
    conn.close()
    return dict(row) if row else None

def delete_session(raw_token):
    if not raw_token:
        return
    conn = get_connection()
    conn.execute("DELETE FROM sessions WHERE token_hash = ?", (_hash_value(raw_token),))
    conn.commit()
    conn.close()


def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    # Authentication tables
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT UNIQUE NOT NULL,
        name TEXT DEFAULT '',
        village TEXT DEFAULT '',
        district TEXT DEFAULT '',
        role TEXT DEFAULT 'citizen',
        created_at TEXT NOT NULL,
        last_login_at TEXT
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS otp_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        phone TEXT NOT NULL,
        otp_hash TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        attempts INTEGER DEFAULT 0,
        used INTEGER DEFAULT 0
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        token_hash TEXT UNIQUE NOT NULL,
        phone TEXT NOT NULL,
        created_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_otp_phone_created ON otp_codes(phone, created_at)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_sessions_token ON sessions(token_hash)")

    # Grievances Table
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS grievances (
        id TEXT PRIMARY KEY,
        citizen_name TEXT NOT NULL,
        phone TEXT NOT NULL,
        district TEXT NOT NULL,
        block TEXT NOT NULL,
        village TEXT NOT NULL,
        gps_location TEXT,
        category TEXT NOT NULL,
        department TEXT NOT NULL,
        assigned_officer TEXT,
        priority TEXT DEFAULT 'Medium',
        sla_hours INTEGER NOT NULL,
        sla_deadline TEXT NOT NULL,
        status TEXT DEFAULT 'New',
        description TEXT NOT NULL,
        photo_before TEXT,
        before_caption TEXT,
        photo_after TEXT,
        after_caption TEXT,
        resolution_notes TEXT,
        submitted_at TEXT NOT NULL,
        resolved_at TEXT,
        citizen_confirmed INTEGER DEFAULT 0,
        satisfaction_rating INTEGER
    )
    """)

    # Stage 1 AI analysis storage. The original grievance text remains in grievances.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS ai_analyses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        complaint_id TEXT NOT NULL,
        category TEXT,
        problem_type TEXT,
        affected_resource TEXT,
        scope TEXT,
        duration TEXT,
        estimated_affected_people INTEGER,
        severity TEXT,
        confidence_score REAL,
        ai_provider TEXT NOT NULL DEFAULT 'rule-based-fallback',
        ai_model TEXT DEFAULT '',
        ai_status TEXT NOT NULL DEFAULT 'fallback',
        ai_error TEXT DEFAULT '',
        raw_analysis TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (complaint_id) REFERENCES grievances(id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_ai_analyses_complaint ON ai_analyses(complaint_id, created_at)")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_phone TEXT NOT NULL,
        action TEXT NOT NULL,
        target_id TEXT,
        details TEXT DEFAULT '',
        created_at TEXT NOT NULL
    )
    """)

    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_role ON users(role)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_users_district_village ON users(district, village)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grievances_status_priority ON grievances(status, priority)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grievances_category ON grievances(category)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_grievances_submitted ON grievances(submitted_at)")

    # Stage 2 underlying-problem clusters. A cluster represents one real-world
    # problem and may contain many differently worded citizen complaints.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS problem_clusters (
        cluster_id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        category TEXT DEFAULT '',
        problem_type TEXT DEFAULT '',
        affected_resource TEXT DEFAULT '',
        scope TEXT DEFAULT '',
        district TEXT DEFAULT '',
        village TEXT DEFAULT '',
        representative_description TEXT DEFAULT '',
        member_count INTEGER DEFAULT 0,
        confidence_score REAL DEFAULT 0,
        ai_provider TEXT DEFAULT 'rule-based-fallback',
        ai_model TEXT DEFAULT '',
        ai_status TEXT DEFAULT 'fallback',
        ai_error TEXT DEFAULT '',
        last_reason TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS problem_cluster_members (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        complaint_id TEXT UNIQUE NOT NULL,
        match_confidence REAL DEFAULT 0,
        match_reason TEXT DEFAULT '',
        ai_provider TEXT DEFAULT 'rule-based-fallback',
        ai_model TEXT DEFAULT '',
        ai_error TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        FOREIGN KEY (cluster_id) REFERENCES problem_clusters(cluster_id),
        FOREIGN KEY (complaint_id) REFERENCES grievances(id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cluster_members_cluster ON problem_cluster_members(cluster_id)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cluster_category_location ON problem_clusters(category, district, village)")

    # Stage 2.5 evidence validation. Kept separate from Stage 2 confidence so
    # validation never destroys the original clustering signal.
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cluster_validations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        cluster_id TEXT NOT NULL,
        stage2_confidence REAL DEFAULT 0,
        validated_confidence REAL DEFAULT 0,
        decision TEXT DEFAULT 'POSSIBLE',
        semantic_evidence REAL DEFAULT 0,
        photo_evidence REAL DEFAULT 0,
        location_evidence REAL DEFAULT 0,
        temporal_evidence REAL DEFAULT 0,
        resource_evidence REAL DEFAULT 0,
        asset_match TEXT DEFAULT 'UNKNOWN',
        reason TEXT DEFAULT '',
        conflicts TEXT DEFAULT '',
        review_required INTEGER DEFAULT 1,
        ai_provider TEXT DEFAULT 'rule-based-fallback',
        ai_model TEXT DEFAULT '',
        ai_error TEXT DEFAULT '',
        member_count INTEGER DEFAULT 0,
        created_at TEXT NOT NULL,
        FOREIGN KEY (cluster_id) REFERENCES problem_clusters(cluster_id)
    )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_cluster_validations_cluster ON cluster_validations(cluster_id, created_at)")

    # Optional developer bootstrap: promote explicitly configured admin phones.
    admin_phones = [
        normalize_phone(p.strip()) for p in os.environ.get("GRAMSETU_ADMIN_PHONE", "").split(",")
        if normalize_phone(p.strip())
    ]
    for admin_phone in admin_phones:
        if len(admin_phone) == 10:
            existing_admin = cursor.execute("SELECT id FROM users WHERE phone = ?", (admin_phone,)).fetchone()
            if existing_admin:
                cursor.execute("UPDATE users SET role = 'admin' WHERE phone = ?", (admin_phone,))
            else:
                now = datetime.now().isoformat()
                cursor.execute(
                    "INSERT INTO users (phone, name, village, district, role, created_at, last_login_at) VALUES (?, '', '', '', 'admin', ?, ?)",
                    (admin_phone, now, now)
                )

    # Seed initial realistic complaints if empty
    cursor.execute("SELECT COUNT(*) as count FROM grievances")
    if cursor.fetchone()["count"] == 0:
        seed_complaints(cursor)

    conn.commit()
    conn.close()

def seed_complaints(cursor):
    sample_data = [
        (
            "GRV-2026-004821", "Rameshwar Patel", "9876543210", "Varanasi", "Sevapuri Tehsil",
            "Rampur Kalan (Ward 4)", "25.3176° N, 82.9739° E", "Electricity",
            "State Electricity Distribution Company (DISCOM)", "Er. R. Sharma (Junior Engineer, Sevapuri)",
            "High", 24, (datetime.now() + timedelta(hours=14)).isoformat(), "In Progress",
            "हमारे गांव रामपुर कलां में मेन ट्रांसफार्मर 3 दिन से जल गया है। पूरे वार्ड 4 में बिजली गुल है और पानी की मोटर भी नहीं चल रही है।",
            "https://images.unsplash.com/photo-1544620347-c4fd4a3d5957?auto=format&fit=crop&w=600&q=80",
            "Burnt 25kVA transformer unit with ruptured coil",
            "", "", "", datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "", 0, None
        ),
        (
            "GRV-2026-003912", "Kavita Devi", "9123456789", "Satara", "Khandala Block",
            "Bavdhan Khurd", "17.9812° N, 73.9856° E", "Water",
            "Rural Water Supply & Sanitation (Jal Shakti)", "S. K. Kadam (Sub-Divisional Officer)",
            "High", 48, (datetime.now() + timedelta(hours=28)).isoformat(), "Resolved",
            "गावच्या मुख्य सार्वजनिक विहिरीची पाइपलाइन फुटल्याने गढूळ पाणी येत आहे. ३०० कुटुंबांना पिण्याचे पाणी नाही.",
            "https://images.unsplash.com/photo-1584467735871-8e85353a8413?auto=format&fit=crop&w=600&q=80",
            "Broken PVC main pipeline flooding the pathway",
            "https://images.unsplash.com/photo-1574482620826-40685ca5ebd2?auto=format&fit=crop&w=600&q=80",
            "New high-density HDPE line welded and water purity test passed",
            "Excavated line at bend, replaced 12 meters of cracked PVC with reinforced HDPE conduit. Chlorination completed in main storage tank.",
            (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"),
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 0, None
        ),
        (
            "GRV-2026-002150", "Harishankar Yadav", "9811223344", "Patna Rural", "Danapur Block",
            "Bikrampur Gram", "25.6120° N, 85.0440° E", "Roads",
            "Public Works Department (PWD / Gram Panchayat)", "A. K. Verma (Assistant Engineer)",
            "Medium", 168, (datetime.now() - timedelta(days=2)).isoformat(), "Closed",
            "मुख्य सड़क पर बरसात के बाद 3 बड़े गड्ढे हो गए हैं। स्कूल की बस फंस जाती है और रात में बाइक सवार गिर जाते हैं।",
            "https://images.unsplash.com/photo-1515162816999-a0c47dc192f7?auto=format&fit=crop&w=600&q=80",
            "Deep potholes on village arterial link road",
            "https://images.unsplash.com/photo-1621905251189-08b45d6a269e?auto=format&fit=crop&w=600&q=80",
            "Bitumen compaction and asphalt overlay completed",
            "Pothole patch repair executed with hot mix asphalt. Leveling and roller compaction completed across 85 meters.",
            (datetime.now() - timedelta(days=4)).strftime("%Y-%m-%d %H:%M:%S"),
            (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S"), 1, 5
        )
    ]

    cursor.executemany("""
    INSERT INTO grievances VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, sample_data)

def get_all_grievances(dept_filter=None, status_filter=None):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM grievances WHERE 1=1"
    params = []

    if dept_filter and dept_filter != "ALL":
        query += " AND category = ?"
        params.append(dept_filter)
    if status_filter and status_filter != "ALL":
        query += " AND status = ?"
        params.append(status_filter)

    query += " ORDER BY submitted_at DESC"
    cursor.execute(query, params)
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return rows

def get_grievance_by_id(gid):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM grievances WHERE id = ?", (gid,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None

def insert_grievance(data):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    INSERT INTO grievances (
        id, citizen_name, phone, district, block, village, gps_location,
        category, department, assigned_officer, priority, sla_hours,
        sla_deadline, status, description, photo_before, before_caption,
        submitted_at
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        data["id"], data["citizen_name"], data["phone"], data["district"],
        data["block"], data["village"], data.get("gps_location", ""),
        data["category"], data["department"], data.get("assigned_officer", "Unassigned"),
        data.get("priority", "Medium"), data["sla_hours"], data["sla_deadline"],
        "New", data["description"], data.get("photo_before", ""),
        data.get("before_caption", "Citizen upload"),
        datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    ))
    conn.commit()
    conn.close()

def save_ai_analysis(complaint_id, analysis):
    """Persist the latest Stage 1 AI extraction for a grievance."""
    import json
    conn = get_connection()
    conn.execute("""
        INSERT INTO ai_analyses (
            complaint_id, category, problem_type, affected_resource, scope,
            duration, estimated_affected_people, severity, confidence_score,
            ai_provider, ai_model, ai_status, ai_error, raw_analysis, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        complaint_id,
        analysis.get("category", ""),
        analysis.get("problem_type", ""),
        analysis.get("affected_resource", ""),
        analysis.get("scope", ""),
        analysis.get("duration", ""),
        analysis.get("estimated_affected_people"),
        analysis.get("severity", ""),
        analysis.get("confidence_score"),
        analysis.get("ai_provider", "rule-based-fallback"),
        analysis.get("ai_model", ""),
        analysis.get("ai_status", "fallback"),
        analysis.get("ai_error", ""),
        json.dumps(analysis, ensure_ascii=False),
        datetime.now().isoformat()
    ))
    conn.commit()
    conn.close()


def get_ai_analysis(complaint_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM ai_analyses
        WHERE complaint_id = ?
        ORDER BY created_at DESC, id DESC LIMIT 1
    """, (complaint_id,)).fetchone()
    conn.close()
    return dict(row) if row else None



def _next_cluster_id(conn):
    year = datetime.now().year
    prefix = f"CL-{year}-"
    row = conn.execute("SELECT cluster_id FROM problem_clusters WHERE cluster_id LIKE ? ORDER BY cluster_id DESC LIMIT 1", (prefix+"%",)).fetchone()
    if not row:
        return prefix + "000001"
    try:
        n = int(row["cluster_id"].split("-")[-1]) + 1
    except Exception:
        n = 1
    return prefix + f"{n:06d}"


def get_cluster_candidates(context, limit=12):
    conn = get_connection()
    category = context.get("category", "")
    district = context.get("district", "")
    village = context.get("village", "")
    rows = conn.execute("""
        SELECT * FROM problem_clusters
        WHERE (? = '' OR category = ?)
          AND (? = '' OR district = ? OR district = '')
        ORDER BY CASE WHEN village = ? THEN 0 ELSE 1 END, updated_at DESC
        LIMIT ?
    """, (category, category, district, district, village, limit)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def create_cluster(title, category, problem_type, affected_resource, scope, district, village,
                   representative_description, confidence, provider, model, error, reason):
    conn = get_connection()
    now = datetime.now().isoformat()
    cluster_id = _next_cluster_id(conn)
    conn.execute("""
        INSERT INTO problem_clusters (
            cluster_id, title, category, problem_type, affected_resource, scope,
            district, village, representative_description, member_count,
            confidence_score, ai_provider, ai_model, ai_status, ai_error,
            last_reason, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (cluster_id, title, category, problem_type, affected_resource, scope,
          district, village, representative_description, confidence, provider,
          model, "success" if provider == "gemini" else "fallback", error, reason, now, now))
    conn.commit()
    conn.close()
    return cluster_id


def add_cluster_member(cluster_id, complaint_id, confidence, reason, provider, model, error):
    conn = get_connection()
    now = datetime.now().isoformat()
    conn.execute("""
        INSERT OR IGNORE INTO problem_cluster_members
        (cluster_id, complaint_id, match_confidence, match_reason, ai_provider, ai_model, ai_error, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (cluster_id, complaint_id, confidence, reason, provider, model, error, now))
    conn.execute("""
        UPDATE problem_clusters
        SET member_count = (SELECT COUNT(*) FROM problem_cluster_members WHERE cluster_id = ?),
            updated_at = ?, confidence_score = ?, ai_provider = ?, ai_model = ?,
            ai_status = ?, ai_error = ?, last_reason = ?
        WHERE cluster_id = ?
    """, (cluster_id, now, confidence, provider, model,
          "success" if provider == "gemini" else "fallback", error, reason, cluster_id))
    conn.commit()
    conn.close()


def touch_cluster(cluster_id, confidence):
    conn = get_connection()
    conn.execute("UPDATE problem_clusters SET updated_at = ?, confidence_score = ? WHERE cluster_id = ?",
                 (datetime.now().isoformat(), confidence, cluster_id))
    conn.commit()
    conn.close()


def get_cluster(cluster_id):
    conn = get_connection()
    row = conn.execute("SELECT * FROM problem_clusters WHERE cluster_id = ?", (cluster_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_all_clusters(search="", category="ALL", district="ALL", limit=200):
    conn = get_connection()
    q = """SELECT pc.*, cv.decision AS validation_decision, cv.validated_confidence,
                      cv.photo_evidence, cv.location_evidence, cv.temporal_evidence,
                      cv.resource_evidence, cv.asset_match, cv.reason AS validation_reason,
                      cv.conflicts AS validation_conflicts, cv.review_required,
                      cv.ai_provider AS validation_provider, cv.created_at AS validated_at
               FROM problem_clusters pc
               LEFT JOIN cluster_validations cv ON cv.id = (
                   SELECT v.id FROM cluster_validations v
                   WHERE v.cluster_id = pc.cluster_id
                   ORDER BY v.created_at DESC, v.id DESC LIMIT 1
               )
               WHERE 1=1"""
    params = []
    if search:
        q += " AND (pc.cluster_id LIKE ? OR pc.title LIKE ? OR pc.village LIKE ? OR pc.problem_type LIKE ?)"
        like = f"%{search}%"
        params += [like, like, like, like]
    if category and category != "ALL":
        q += " AND pc.category = ?"; params.append(category)
    if district and district != "ALL":
        q += " AND pc.district = ?"; params.append(district)
    q += " ORDER BY pc.updated_at DESC LIMIT ?"; params.append(int(limit))
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


def get_cluster_members(cluster_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.*, g.description, g.village AS complaint_village, g.district AS complaint_district,
               g.submitted_at, g.category
        FROM problem_cluster_members m
        JOIN grievances g ON g.id = m.complaint_id
        WHERE m.cluster_id = ? ORDER BY m.created_at ASC
    """, (cluster_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_cluster_members_with_evidence(cluster_id):
    conn = get_connection()
    rows = conn.execute("""
        SELECT m.*, g.description, g.village AS complaint_village, g.district AS complaint_district,
               g.submitted_at, g.category, g.photo_before, g.before_caption, g.gps_location,
               a.problem_type, a.affected_resource, a.scope
        FROM problem_cluster_members m
        JOIN grievances g ON g.id = m.complaint_id
        LEFT JOIN ai_analyses a ON a.id = (
            SELECT aa.id FROM ai_analyses aa WHERE aa.complaint_id = g.id ORDER BY aa.created_at DESC, aa.id DESC LIMIT 1
        )
        WHERE m.cluster_id = ? ORDER BY m.created_at ASC
    """, (cluster_id,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def cluster_stats():
    conn = get_connection()
    row = conn.execute("""
        SELECT COUNT(*) AS clusters,
               COALESCE(SUM(member_count),0) AS clustered_complaints,
               COALESCE(MAX(member_count),0) AS largest_cluster
        FROM problem_clusters
    """).fetchone()
    conn.close()
    return dict(row)


def save_cluster_validation(cluster_id, result):
    conn = get_connection()
    now = result.get("validated_at") or datetime.now().isoformat()
    conn.execute("""
        INSERT INTO cluster_validations (
            cluster_id, stage2_confidence, validated_confidence, decision,
            semantic_evidence, photo_evidence, location_evidence, temporal_evidence,
            resource_evidence, asset_match, reason, conflicts, review_required,
            ai_provider, ai_model, ai_error, member_count, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        cluster_id, result.get("stage2_confidence", 0), result.get("validated_confidence", 0),
        result.get("decision", "POSSIBLE"), result.get("semantic_evidence", 0),
        result.get("photo_evidence", 0), result.get("location_evidence", 0),
        result.get("temporal_evidence", 0), result.get("resource_evidence", 0),
        result.get("asset_match", "UNKNOWN"), result.get("reason", ""),
        result.get("conflicts", ""), 1 if result.get("review_required", True) else 0,
        result.get("ai_provider", "rule-based-fallback"), result.get("ai_model", ""),
        result.get("ai_error", ""), result.get("member_count", 0), now
    ))
    conn.commit()
    conn.close()


def get_cluster_validation(cluster_id):
    conn = get_connection()
    row = conn.execute("""
        SELECT * FROM cluster_validations
        WHERE cluster_id = ? ORDER BY created_at DESC, id DESC LIMIT 1
    """, (cluster_id,)).fetchone()
    conn.close()
    return dict(row) if row else None


def get_cluster_validation_history(cluster_id, limit=10):
    conn = get_connection()
    rows = conn.execute("""
        SELECT * FROM cluster_validations
        WHERE cluster_id = ? ORDER BY created_at DESC, id DESC LIMIT ?
    """, (cluster_id, int(limit))).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_official_resolution(gid, status, officer, photo_after, after_caption, notes):
    conn = get_connection()
    cursor = conn.cursor()
    resolved_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S") if status == "Resolved" else ""
    cursor.execute("""
    UPDATE grievances SET
        status = ?,
        assigned_officer = ?,
        photo_after = ?,
        after_caption = ?,
        resolution_notes = ?,
        resolved_at = ?
    WHERE id = ?
    """, (status, officer, photo_after, after_caption, notes, resolved_at, gid))
    conn.commit()
    conn.close()

def citizen_confirm_grievance(gid, is_confirmed, rating=5, feedback=""):
    conn = get_connection()
    cursor = conn.cursor()
    if is_confirmed:
        cursor.execute("""
        UPDATE grievances SET
            status = 'Closed',
            citizen_confirmed = 1,
            satisfaction_rating = ?
        WHERE id = ?
        """, (rating, gid))
    else:
        cursor.execute("""
        UPDATE grievances SET
            status = 'In Progress',
            priority = 'Emergency Escalation',
            citizen_confirmed = 0,
            resolution_notes = resolution_notes || ' [REOPENED BY CITIZEN: Problem persists]'
        WHERE id = ?
        """, (gid,))
    conn.commit()
    conn.close()


def _safe_int(value, default=0):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def admin_stats():
    conn = get_connection()
    c = conn.cursor()
    total = c.execute("SELECT COUNT(*) FROM grievances").fetchone()[0]
    users = c.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    high = c.execute("SELECT COUNT(*) FROM grievances WHERE priority IN ('High','Emergency Escalation')").fetchone()[0]
    pending = c.execute("SELECT COUNT(*) FROM grievances WHERE status != 'Closed'").fetchone()[0]
    resolved = c.execute("SELECT COUNT(*) FROM grievances WHERE status IN ('Resolved','Closed')").fetchone()[0]
    data = {
        "total_complaints": total, "citizens": users, "high_priority": high,
        "pending": pending, "resolved": resolved,
        "status": [dict(r) for r in c.execute("SELECT status, COUNT(*) count FROM grievances GROUP BY status ORDER BY count DESC")],
        "priority": [dict(r) for r in c.execute("SELECT priority, COUNT(*) count FROM grievances GROUP BY priority ORDER BY count DESC")],
        "categories": [dict(r) for r in c.execute("SELECT category, COUNT(*) count FROM grievances GROUP BY category ORDER BY count DESC")],
        "districts": [dict(r) for r in c.execute("SELECT district, COUNT(*) count FROM grievances GROUP BY district ORDER BY count DESC")]
    }
    conn.close()
    return data


def admin_list_grievances(search="", status="", priority="", category="", district="", village="", limit=200):
    conn = get_connection()
    q = """SELECT g.*,
        a.category AS ai_category,
        a.problem_type AS ai_problem_type,
        a.affected_resource AS ai_affected_resource,
        a.scope AS ai_scope,
        a.duration AS ai_duration,
        a.estimated_affected_people AS ai_estimated_affected_people,
        a.severity AS ai_severity,
        a.confidence_score AS ai_confidence_score,
        a.ai_provider AS ai_provider,
        a.ai_model AS ai_model,
        a.ai_status AS ai_status,
        a.ai_error AS ai_error,
        a.created_at AS ai_analyzed_at
        FROM grievances g
        LEFT JOIN ai_analyses a ON a.id = (
            SELECT id FROM ai_analyses aa
            WHERE aa.complaint_id = g.id
            ORDER BY aa.created_at DESC, aa.id DESC LIMIT 1
        )
        WHERE 1=1"""

    params = []
    if search:
        term = f"%{search}%"
        q += " AND (id LIKE ? OR citizen_name LIKE ? OR phone LIKE ? OR description LIKE ? OR village LIKE ? OR district LIKE ? OR category LIKE ? OR department LIKE ?)"
        params.extend([term] * 8)
    for field, value in [("status", status), ("priority", priority), ("category", category), ("district", district), ("village", village)]:
        if value and value != "ALL":
            q += f" AND {field} = ?"
            params.append(value)
    q += " ORDER BY submitted_at DESC LIMIT ?"
    params.append(max(1, min(_safe_int(limit, 200), 1000)))
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


def admin_list_users(search="", district="", role="", limit=200):
    conn = get_connection()
    q = "SELECT id, phone, name, village, district, role, created_at, last_login_at FROM users WHERE 1=1"
    params = []
    if search:
        term = f"%{search}%"
        q += " AND (phone LIKE ? OR name LIKE ? OR village LIKE ? OR district LIKE ?)"
        params.extend([term] * 4)
    if district and district != "ALL":
        q += " AND district = ?"
        params.append(district)
    if role and role != "ALL":
        q += " AND role = ?"
        params.append(role)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(max(1, min(_safe_int(limit, 200), 1000)))
    rows = [dict(r) for r in conn.execute(q, params).fetchall()]
    conn.close()
    return rows


def admin_update_grievance(gid, status=None, priority=None, assigned_officer=None, notes=None):
    allowed_status = {"New", "In Progress", "Resolved", "Closed"}
    allowed_priority = {"Low", "Medium", "High", "Emergency Escalation"}
    updates, params = [], []
    if status is not None:
        if status not in allowed_status:
            raise ValueError("Invalid status.")
        updates.append("status = ?"); params.append(status)
        if status == "Resolved":
            updates.append("resolved_at = COALESCE(resolved_at, ?)")
            params.append(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    if priority is not None:
        if priority not in allowed_priority:
            raise ValueError("Invalid priority.")
        updates.append("priority = ?"); params.append(priority)
    if assigned_officer is not None:
        updates.append("assigned_officer = ?"); params.append(str(assigned_officer).strip())
    if notes is not None:
        updates.append("resolution_notes = ?"); params.append(str(notes))
    if not updates:
        raise ValueError("No changes supplied.")
    conn = get_connection()
    params.append(gid)
    c = conn.cursor()
    c.execute(f"UPDATE grievances SET {', '.join(updates)} WHERE id = ?", params)
    changed = c.rowcount
    conn.commit(); conn.close()
    return changed > 0


def admin_audit(admin_phone, action, target_id="", details=""):
    conn = get_connection()
    conn.execute(
        "INSERT INTO admin_audit_log (admin_phone, action, target_id, details, created_at) VALUES (?, ?, ?, ?, ?)",
        (normalize_phone(admin_phone), action, target_id, details, datetime.now().isoformat())
    )
    conn.commit(); conn.close()


def admin_recent_audit(limit=50):
    conn = get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM admin_audit_log ORDER BY created_at DESC LIMIT ?", (max(1, min(_safe_int(limit, 50), 200)),)
    ).fetchall()]
    conn.close()
    return rows


def admin_database_summary():
    conn = get_connection()
    names = ["users", "grievances", "ai_analyses", "otp_codes", "sessions", "admin_audit_log"]
    tables = [{"table": n, "rows": conn.execute(f"SELECT COUNT(*) FROM {n}").fetchone()[0]} for n in names]
    size = os.path.getsize(DB_PATH) if os.path.exists(DB_PATH) else 0
    conn.close()
    return {"path": DB_PATH, "size_bytes": size, "tables": tables}


def admin_distinct_values():
    conn = get_connection()
    result = {}
    for field in ["status", "priority", "category", "district", "village"]:
        result[field] = [r[0] for r in conn.execute(
            f"SELECT DISTINCT {field} FROM grievances WHERE {field} IS NOT NULL AND {field} != '' ORDER BY {field}"
        ).fetchall()]
    conn.close()
    return result
