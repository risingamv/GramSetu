"""
GramSetu (ग्राम सेतु) - Rural AI Grievance Redressal Server
Lightweight HTTP REST API & static web server using Python standard library.
Zero external dependencies required.
"""

import http.server
import socketserver
import json
import os
import urllib.parse
import secrets
import re
from datetime import datetime
import database as db_module
from database import hash_otp, normalize_phone, create_otp, verify_otp, upsert_user, create_session, get_user_by_session, delete_session
from database import (
    init_db, get_all_grievances, get_grievance_by_id,
    insert_grievance, update_official_resolution, citizen_confirm_grievance,
    save_ai_analysis, get_ai_analysis,
    get_all_clusters, get_cluster, get_cluster_members, cluster_stats,
    admin_stats, admin_list_grievances, admin_list_users, admin_update_grievance,
    admin_audit, admin_recent_audit, admin_database_summary, admin_distinct_values,
    get_cluster_validation, get_cluster_validation_history, get_cluster_members_with_evidence
)
from ai_classifier import classify_complaint, generate_complaint_id
from stage2_cluster import assign_complaint_to_cluster
from stage2_5_validation import validate_cluster

PORT = 8080
PUBLIC_DIR = os.path.join(os.path.dirname(__file__), "public")

def load_dotenv_file():
    """Load simple KEY=VALUE settings from a local .env file if present."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return
    try:
        with open(env_path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('\"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError as exc:
        print(f"Could not read .env: {exc}")

load_dotenv_file()

class GrievanceAPIHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=PUBLIC_DIR, **kwargs)


    def get_session_token(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            if part.strip().startswith("gramsetu_session="):
                return part.strip().split("=", 1)[1]
        return ""

    def current_user(self):
        return get_user_by_session(self.get_session_token())

    def require_user(self):
        user = self.current_user()
        if not user:
            self.send_json({"status": "error", "message": "Authentication required."}, 401)
            return None
        return user

    def require_admin(self):
        user = self.current_user()
        if not user:
            self.send_json({"status": "error", "message": "Authentication required."}, 401)
            return None
        if user.get("role") != "admin":
            self.send_json({"status": "error", "message": "Administrator access required."}, 403)
            return None
        return user

    def send_csv(self, rows, filename="gramsetu-export.csv"):
        import csv
        import io
        output = io.StringIO()
        if not rows:
            output.write("No records\\n")
        else:
            writer = csv.DictWriter(output, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        body = output.getvalue().encode("utf-8-sig")
        self.send_response(200)
        self.send_header("Content-Type", "text/csv; charset=utf-8")
        self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def set_session_cookie(self, token):
        self.send_header(
            "Set-Cookie",
            f"gramsetu_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age=604800"
        )

    def clear_session_cookie(self):
        self.send_header(
            "Set-Cookie",
            "gramsetu_session=; HttpOnly; SameSite=Lax; Path=/; Max-Age=0"
        )

    def send_sms_otp(self, phone, otp):
        # Production delivery hook. Set GRAMSETU_SMS_WEBHOOK_URL to your
        # SMS provider/own backend. The webhook receives JSON with phone/message.
        webhook = os.environ.get("GRAMSETU_SMS_WEBHOOK_URL", "").strip()
        if not webhook:
            return False
        import urllib.request
        payload = json.dumps({
            "phone": f"+91{phone}",
            "message": f"Your GramSetu login OTP is {otp}. It expires in 5 minutes."
        }).encode("utf-8")
        request = urllib.request.Request(
            webhook, data=payload,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=8) as response:
                return 200 <= response.status < 300
        except Exception as exc:
            print(f"SMS delivery failed: {exc}")
            return False

    def send_json(self, data, status_code=200):
        self.send_response(status_code)
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        if path == "/api/auth/me":
            user = self.current_user()
            if user:
                self.send_json({"status": "authenticated", "user": user})
            else:
                self.send_json({"status": "unauthenticated"}, 401)
            return

        # Server-side protected admin APIs. The browser cannot bypass require_admin().
        if path == "/api/admin/ai-status":
            admin = self.require_admin()
            if not admin:
                return
            key = os.environ.get("GEMINI_API_KEY", "").strip()
            model = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite").strip() or "gemini-3.5-flash-lite"
            if model == "gemini-2.5-flash-lite":
                model = "gemini-3.5-flash-lite"
            self.send_json({
                "status": "success",
                "data": {
                    # Never return any portion or metadata of the secret key.
                    "gemini_key_loaded": bool(key),
                    "gemini_model": model,
                    "env_file": os.path.isfile(os.path.join(os.path.dirname(__file__), ".env")),
                }
            })
            return

        if path == "/api/admin/stats":
            if not self.require_admin(): return
            self.send_json({"status": "success", "data": admin_stats()})
            return

        if path == "/api/admin/filters":
            if not self.require_admin(): return
            self.send_json({"status": "success", "data": admin_distinct_values()})
            return

        if path == "/api/admin/complaints":
            if not self.require_admin(): return
            p = urllib.parse.parse_qs(parsed.query)
            rows = admin_list_grievances(
                search=p.get("search", [""])[0],
                status=p.get("status", [""])[0],
                priority=p.get("priority", [""])[0],
                category=p.get("category", [""])[0],
                district=p.get("district", [""])[0],
                village=p.get("village", [""])[0],
                limit=p.get("limit", ["200"])[0]
            )
            self.send_json({"status": "success", "count": len(rows), "data": rows})
            return

        if path == "/api/admin/users":
            if not self.require_admin(): return
            p = urllib.parse.parse_qs(parsed.query)
            rows = admin_list_users(
                search=p.get("search", [""])[0],
                district=p.get("district", [""])[0],
                role=p.get("role", [""])[0],
                limit=p.get("limit", ["200"])[0]
            )
            self.send_json({"status": "success", "count": len(rows), "data": rows})
            return

        if path == "/api/admin/audit":
            if not self.require_admin(): return
            p = urllib.parse.parse_qs(parsed.query)
            self.send_json({"status": "success", "data": admin_recent_audit(p.get("limit", ["50"])[0])})
            return

        if path == "/api/admin/database":
            if not self.require_admin(): return
            self.send_json({"status": "success", "data": admin_database_summary()})
            return

        if path == "/api/admin/export/complaints":
            if not self.require_admin(): return
            p = urllib.parse.parse_qs(parsed.query)
            rows = admin_list_grievances(
                search=p.get("search", [""])[0], status=p.get("status", [""])[0],
                priority=p.get("priority", [""])[0], category=p.get("category", [""])[0],
                district=p.get("district", [""])[0], village=p.get("village", [""])[0], limit=1000
            )
            admin_audit(self.current_user()["phone"], "export_complaints", "", f"{len(rows)} rows")
            self.send_csv(rows, "gramsetu-complaints.csv")
            return

        if path.startswith("/api/admin/complaints/") and path.endswith("/ai"):
            admin = self.require_admin()
            if not admin:
                return
            gid = path.split("/api/admin/complaints/", 1)[1][:-3].strip("/")
            analysis = get_ai_analysis(gid)
            if analysis:
                self.send_json({"status": "success", "data": analysis})
            else:
                self.send_json({"status": "error", "message": "No AI analysis found for this complaint."}, 404)
            return

        if path == "/api/admin/clusters":
            admin = self.require_admin()
            if not admin:
                return
            params = urllib.parse.parse_qs(parsed.query)
            rows = get_all_clusters(
                search=params.get("search", [""])[0],
                category=params.get("category", ["ALL"])[0],
                district=params.get("district", ["ALL"])[0],
                limit=min(int(params.get("limit", ["200"])[0]), 500)
            )
            self.send_json({"status": "success", "count": len(rows), "data": rows, "stats": cluster_stats()})
            return

        if path.startswith("/api/admin/clusters/"):
            admin = self.require_admin()
            if not admin:
                return
            cid = path.split("/api/admin/clusters/", 1)[1].strip("/")
            cluster = get_cluster(cid)
            if not cluster:
                self.send_json({"status": "error", "message": "Problem cluster not found."}, 404)
                return
            cluster["members"] = get_cluster_members(cid)
            cluster["validation"] = get_cluster_validation(cid)
            self.send_json({"status": "success", "data": cluster})
            return

        if path == "/api/grievances":
            # Complaint lists are private. Citizens can only see their own reports;
            # administrators can see all reports through the protected admin API.
            user = self.require_user()
            if not user:
                return
            params = urllib.parse.parse_qs(parsed.query)
            dept = params.get("dept", [None])[0]
            status = params.get("status", [None])[0]
            items = get_all_grievances(dept, status)
            if user.get("role") != "admin":
                items = [item for item in items if str(item.get("phone", "")) == str(user.get("phone", ""))]
            self.send_json({"status": "success", "count": len(items), "data": items})
            return

        if path.startswith("/api/grievances/"):
            # Never disclose another citizen's report by guessing its ID.
            user = self.require_user()
            if not user:
                return
            gid = urllib.parse.unquote(path.split("/api/grievances/", 1)[1]).strip("/")
            g = get_grievance_by_id(gid)
            if not g:
                self.send_json({"status": "error", "message": "Grievance not found"}, 404)
            elif user.get("role") != "admin" and str(g.get("phone", "")) != str(user.get("phone", "")):
                # Use the same response as an unknown ID to avoid confirming report IDs.
                self.send_json({"status": "error", "message": "Grievance not found"}, 404)
            else:
                self.send_json({"status": "success", "data": g})
            return

        # Serve static frontend
        # Never let the browser cache the admin UI during local development.
        if path == "/" or path.endswith(".html") or path.endswith(".js") or path.endswith(".css"):
            self.send_response(200)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "text/html; charset=utf-8" if path == "/" or path.endswith(".html") else ("application/javascript; charset=utf-8" if path.endswith(".js") else "text/css; charset=utf-8"))
            file_path = os.path.join(PUBLIC_DIR, "index.html" if path == "/" else path.lstrip("/"))
            if os.path.isfile(file_path):
                with open(file_path, "rb") as f:
                    body = f.read()
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_error(404)
            return
        return super().do_GET()

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        content_len = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_len).decode("utf-8")
        data = json.loads(body) if body else {}

        # Request a one-time password for passwordless citizen login.
        if path == "/api/auth/request-otp":
            phone = normalize_phone(data.get("phone", ""))
            name = str(data.get("name", "")).strip()
            village = str(data.get("village", "")).strip()
            district = str(data.get("district", "")).strip()

            if len(phone) != 10 or not phone.isdigit():
                self.send_json({"status": "error", "message": "Enter a valid 10-digit Indian mobile number."}, 400)
                return

            otp = f"{secrets.randbelow(1000000):06d}"
            try:
                expires_at = create_otp(phone, hash_otp(otp))
            except ValueError as exc:
                self.send_json({"status": "error", "message": str(exc)}, 429)
                return

            # Store/update the citizen record immediately; OTP verification is still
            # required before a session is created.
            upsert_user(phone, name=name, village=village, district=district, role="citizen")

            delivered = self.send_sms_otp(phone, otp)
            dev_mode = os.environ.get("GRAMSETU_ENV", "development").lower() != "production"

            if not delivered and not dev_mode:
                self.send_json({
                    "status": "error",
                    "message": "OTP could not be delivered. Configure GRAMSETU_SMS_WEBHOOK_URL."
                }, 503)
                return

            response = {
                "status": "success",
                "message": "OTP generated. Check your mobile for the code.",
                "expires_at": expires_at
            }
            # Development mode intentionally exposes the OTP so the local prototype
            # can be tested without an SMS provider. Never enable this in production.
            if dev_mode:
                response["dev_otp"] = otp
                response["message"] = "Development OTP generated. In production, the OTP is sent by SMS."
            self.send_json(response)
            return

        # Verify OTP and create an HttpOnly session cookie.
        if path == "/api/auth/verify-otp":
            phone = normalize_phone(data.get("phone", ""))
            otp = str(data.get("otp", "")).strip()
            if len(phone) != 10 or not phone.isdigit() or not re.fullmatch(r"\d{6}", otp):
                self.send_json({"status": "error", "message": "Enter the 10-digit phone number and 6-digit OTP."}, 400)
                return

            ok, message = verify_otp(phone, hash_otp(otp))
            if not ok:
                self.send_json({"status": "error", "message": message}, 401)
                return

            user = upsert_user(
                phone,
                name=str(data.get("name", "")).strip(),
                village=str(data.get("village", "")).strip(),
                district=str(data.get("district", "")).strip(),
                role="citizen"
            )
            token = create_session(phone)

            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")
            self.set_session_cookie(token)
            self.end_headers()
            self.wfile.write(json.dumps({
                "status": "success",
                "message": "Login successful.",
                "user": user
            }).encode("utf-8"))
            return

        if path == "/api/auth/logout":
            delete_session(self.get_session_token())
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.clear_session_cookie()
            self.end_headers()
            self.wfile.write(json.dumps({"status": "success", "message": "Logged out."}).encode("utf-8"))
            return

        # AI live classification endpoint
        if path == "/api/ai/classify":
            user = self.require_user()
            if not user:
                return
            text = str(data.get("text", "")).strip()
            if not text:
                self.send_json({"status": "error", "message": "Complaint text is required."}, 400)
                return
            res = classify_complaint(
                text,
                village=str(data.get("village", "")).strip(),
                district=str(data.get("district", "")).strip(),
                category_hint=str(data.get("category", "")).strip()
            )
            self.send_json({"status": "success", "analysis": res})
            return

        # Submit new grievance
        if path == "/api/auth/me":
            user = self.current_user()
            if user:
                self.send_json({"status": "authenticated", "user": user})
            else:
                self.send_json({"status": "unauthenticated"}, 401)
            return

        if path == "/api/admin/clusters":
            admin = self.require_admin()
            if not admin:
                return
            params = urllib.parse.parse_qs(parsed.query)
            rows = get_all_clusters(
                search=params.get("search", [""])[0],
                category=params.get("category", ["ALL"])[0],
                district=params.get("district", ["ALL"])[0],
                limit=min(int(params.get("limit", ["200"])[0]), 500)
            )
            self.send_json({"status": "success", "count": len(rows), "data": rows, "stats": cluster_stats()})
            return

        if path.startswith("/api/admin/clusters/"):
            admin = self.require_admin()
            if not admin:
                return
            cid = path.split("/api/admin/clusters/", 1)[1].strip("/")
            cluster = get_cluster(cid)
            if not cluster:
                self.send_json({"status": "error", "message": "Problem cluster not found."}, 404)
                return
            cluster["members"] = get_cluster_members(cid)
            cluster["validation"] = get_cluster_validation(cid)
            self.send_json({"status": "success", "data": cluster})
            return

        if path == "/api/grievances":
            user = self.require_user()
            if not user:
                return
            desc = str(data.get("description", "")).strip()
            if not desc:
                self.send_json({"status": "error", "message": "Complaint description is required."}, 400)
                return

            village = str(data.get("village", "")).strip()
            district = str(data.get("district", "Rural")).strip()
            category_hint = str(data.get("category", "")).strip()

            # Stage 1: analyze the complaint with Gemini (or safe local fallback).
            # Only complaint/location context is sent; citizen phone/name are not.
            ai_meta = classify_complaint(
                desc,
                village=village,
                district=district,
                category_hint=category_hint
            )
            gid = generate_complaint_id()

            record = {
                "id": gid,
                "citizen_name": data.get("name") or user.get("name") or "Citizen",
                "phone": user["phone"],
                "district": district,
                "block": data.get("block", ""),
                "village": village,
                "gps_location": data.get("gps", ""),
                "category": ai_meta["category"],
                "department": ai_meta["department"],
                "assigned_officer": ai_meta["assigned_officer"],
                "priority": ai_meta["priority"],
                "sla_hours": ai_meta["sla_hours"],
                "sla_deadline": ai_meta["sla_deadline"],
                "description": desc,
                "photo_before": data.get("photo_before", ""),
                "before_caption": data.get("before_caption", "Initial citizen evidence")
            }
            insert_grievance(record)
            save_ai_analysis(gid, ai_meta)

            # Stage 2: group this complaint with an existing underlying problem
            # when evidence is strong enough; otherwise create a new cluster.
            cluster = None
            try:
                cluster = assign_complaint_to_cluster(record, ai_meta, db_module)
                # Stage 2.5: once at least two reports exist in a cluster, validate
                # the grouping with independent evidence such as photos, location,
                # time and affected resource. Validation never changes official priority.
                if cluster and int(cluster.get("member_count") or 0) >= 2:
                    members = db_module.get_cluster_members_with_evidence(cluster["cluster_id"])
                    validate_cluster(cluster, members, db_module)
                    cluster = db_module.get_cluster(cluster["cluster_id"])
                    cluster["validation"] = get_cluster_validation(cluster["cluster_id"])
            except Exception as exc:
                # AI validation must never block complaint registration.
                print(f"Stage 2/2.5 processing failed for {gid}: {exc}")

            # AI fields are returned for the citizen-facing success state and for testing,
            # but the original complaint remains unchanged in the grievance record.
            self.send_json({
                "status": "created",
                "complaint_id": gid,
                "record": record,
                "ai_analysis": ai_meta,
                "stage2_cluster": cluster
            }, 201)
            return

        if path == "/api/admin/stage2_5/validate":
            admin = self.require_admin()
            if not admin:
                return
            cid = str(data.get("cluster_id", "")).strip()
            cluster = db_module.get_cluster(cid)
            if not cluster:
                self.send_json({"status": "error", "message": "Problem cluster not found."}, 404)
                return
            try:
                members = db_module.get_cluster_members_with_evidence(cid)
                if len(members) < 2:
                    self.send_json({"status": "error", "message": "Stage 2.5 needs at least two reports in the cluster."}, 400)
                    return
                validation = validate_cluster(cluster, members, db_module)
                admin_audit(admin["phone"], "stage2_5_validate", cid, validation.get("decision", ""))
                self.send_json({"status": "success", "data": validation})
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 500)
            return

        if path == "/api/admin/stage2_5/history":
            admin = self.require_admin()
            if not admin:
                return
            cid = str(data.get("cluster_id", "")).strip()
            self.send_json({"status": "success", "data": get_cluster_validation_history(cid)})
            return

        if path == "/api/admin/stage2/cluster":
            admin = self.require_admin()
            if not admin:
                return
            gid = str(data.get("complaint_id", "")).strip()
            g = get_grievance_by_id(gid)
            a = get_ai_analysis(gid)
            if not g:
                self.send_json({"status": "error", "message": "Complaint not found."}, 404)
                return
            if not a:
                self.send_json({"status": "error", "message": "Stage 1 AI analysis not found for this complaint."}, 400)
                return
            try:
                cluster = assign_complaint_to_cluster(g, a, db_module)
                validation = None
                if cluster and int(cluster.get("member_count") or 0) >= 2:
                    validation = validate_cluster(cluster, db_module.get_cluster_members_with_evidence(cluster["cluster_id"]), db_module)
                if cluster:
                    cluster["validation"] = validation or get_cluster_validation(cluster["cluster_id"])
                admin_audit(admin["phone"], "stage2_cluster", gid, cluster["cluster_id"] if cluster else "")
                self.send_json({"status": "success", "data": cluster})
            except Exception as exc:
                self.send_json({"status": "error", "message": str(exc)}, 500)
            return

        if path.startswith("/api/admin/complaints/") and path != "/api/admin/complaints/":
            admin = self.require_admin()
            if not admin:
                return
            gid = path.split("/api/admin/complaints/", 1)[1].strip("/")
            try:
                changed = admin_update_grievance(
                    gid,
                    status=data.get("status"),
                    priority=data.get("priority"),
                    assigned_officer=data.get("assigned_officer"),
                    notes=data.get("notes")
                )
            except ValueError as exc:
                self.send_json({"status": "error", "message": str(exc)}, 400)
                return
            if not changed:
                self.send_json({"status": "error", "message": "Complaint not found."}, 404)
                return
            admin_audit(admin["phone"], "update_complaint", gid, json.dumps(data, ensure_ascii=False))
            self.send_json({"status": "success", "message": f"{gid} updated."})
            return

        # Official resolution update
        if "/resolve" in path:
            gid = path.split("/api/grievances/")[1].split("/resolve")[0]
            update_official_resolution(
                gid=gid,
                status=data.get("status", "Resolved"),
                officer=data.get("officer", ""),
                photo_after=data.get("photo_after", ""),
                after_caption=data.get("after_caption", "Resolution verified"),
                notes=data.get("notes", "")
            )
            self.send_json({"status": "success", "message": f"Docket {gid} updated"})
            return

        # Citizen confirmation loop
        if "/confirm" in path:
            gid = path.split("/api/grievances/")[1].split("/confirm")[0]
            citizen_confirm_grievance(
                gid=gid,
                is_confirmed=data.get("confirmed", True),
                rating=data.get("rating", 5),
                feedback=data.get("feedback", "")
            )
            self.send_json({"status": "success", "message": f"Citizen verification recorded for {gid}"})
            return

        self.send_json({"status": "error", "message": "Unknown endpoint"}, 404)

def run_server():
    init_db()
    os.makedirs(PUBLIC_DIR, exist_ok=True)
    with socketserver.TCPServer(("", PORT), GrievanceAPIHandler) as httpd:
        print(f"GramSetu Grievance Server running on http://localhost:{PORT}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server.")

if __name__ == "__main__":
    run_server()
