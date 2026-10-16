import hmac
import os
import secrets
import sqlite3
import uuid
import mimetypes
import re
import urllib.error
import urllib.parse
import urllib.request
from contextlib import contextmanager
from functools import wraps
from pathlib import Path
from threading import Lock
from io import BytesIO

import click
from flask import (
    Flask,
    abort,
    flash,
    g,
    redirect,
    render_template,
    request,
    send_from_directory,
    send_file,
    session,
    url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash
from werkzeug.utils import secure_filename

import psycopg

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "nutrix.db"
CREDENTIALS_DIR = BASE_DIR / "private_credentials"
ALLOWED_CREDENTIAL_EXTENSIONS = {".pdf", ".jpg", ".jpeg", ".png"}
BOOKING_STATUSES = {"Pending", "Confirmed", "Completed", "Cancelled"}
STORAGE_BUCKET = os.environ.get("SUPABASE_STORAGE_BUCKET", "verification-documents")
_database_initialized = False
_database_initialization_lock = Lock()

app = Flask(__name__, static_folder=None)
app.secret_key = os.environ.get("NUTRIX_SECRET_KEY") or secrets.token_hex(32)
app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
app.config["SESSION_COOKIE_SECURE"] = bool(os.environ.get("VERCEL"))


class DatabaseRow(dict):
    def __getitem__(self, key):
        if isinstance(key, int):
            return tuple(self.values())[key]
        return super().__getitem__(key)


class DatabaseCursor:
    def __init__(self, cursor, postgres=False):
        self.cursor = cursor
        self.postgres = postgres

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def _convert(self, row):
        if row is None:
            return None
        if not self.postgres:
            return row
        names = [column.name for column in self.cursor.description]
        return DatabaseRow(zip(names, row))

    def fetchone(self):
        return self._convert(self.cursor.fetchone())

    def fetchall(self):
        return [self._convert(row) for row in self.cursor.fetchall()]


class DatabaseConnection:
    def __init__(self, connection, postgres=False):
        self.connection = connection
        self.postgres = postgres

    def execute(self, sql, parameters=()):
        if self.postgres:
            sql = sql.replace("?", "%s")
        cursor = self.connection.cursor()
        cursor.execute(sql, parameters)
        return DatabaseCursor(cursor, self.postgres)

    def executescript(self, script):
        for statement in script.split(";"):
            if statement.strip():
                self.execute(statement)

    def commit(self):
        self.connection.commit()

    def rollback(self):
        self.connection.rollback()

    def close(self):
        self.connection.close()


@contextmanager
def get_db():
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if os.environ.get("VERCEL") and not database_url:
        raise RuntimeError("Vercel deployment requires a Supabase DATABASE_URL.")
    postgres = database_url.startswith(("postgres://", "postgresql://"))
    if database_url and not postgres:
        raise RuntimeError("DATABASE_URL must be a PostgreSQL connection URL.")
    if postgres:
        import psycopg

        url = database_url.replace("postgres://", "postgresql://", 1)
        raw_connection = psycopg.connect(url, prepare_threshold=None)
        connection = DatabaseConnection(raw_connection, postgres=True)
    else:
        raw_connection = sqlite3.connect(DB_PATH)
        raw_connection.row_factory = sqlite3.Row
        connection = DatabaseConnection(raw_connection)
        connection.execute("PRAGMA foreign_keys = ON")
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def ensure_column(db, table, name, definition):
    if db.postgres:
        columns = {
            row["column_name"]
            for row in db.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = ?",
                (table,),
            ).fetchall()
        }
    else:
        columns = {
            row["name"]
            for row in db.execute(f"PRAGMA table_info({table})").fetchall()
        }
    if name not in columns:
        db.execute(f"ALTER TABLE {table} ADD COLUMN {name} {definition}")


def initialize_database():
    with get_db() as db:
        schema = """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL CHECK(role IN ('patient', 'caregiver', 'admin')),
                name TEXT NOT NULL,
                verification_status TEXT NOT NULL DEFAULT 'pending',
                credential_path TEXT,
                review_note TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS patients (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                age INTEGER,
                condition TEXT,
                phone TEXT,
                address TEXT
            );

            CREATE TABLE IF NOT EXISTS caregivers (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                role TEXT NOT NULL,
                phone TEXT,
                experience INTEGER,
                available TEXT DEFAULT 'Available'
            );

            CREATE TABLE IF NOT EXISTS bookings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                caregiver_id INTEGER NOT NULL,
                date TEXT NOT NULL,
                time TEXT NOT NULL,
                visit_location TEXT NOT NULL DEFAULT '',
                health_description TEXT NOT NULL DEFAULT '',
                status TEXT DEFAULT 'Pending',
                submission_token TEXT,
                FOREIGN KEY(patient_id) REFERENCES patients(id),
                FOREIGN KEY(caregiver_id) REFERENCES caregivers(id)
            );

            CREATE TABLE IF NOT EXISTS care_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                log_date TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(patient_id) REFERENCES patients(id)
            );

            CREATE TABLE IF NOT EXISTS care_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                patient_id INTEGER NOT NULL,
                summary TEXT NOT NULL,
                city TEXT NOT NULL,
                schedule TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Open',
                created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY(patient_id) REFERENCES patients(id)
            );
        """
        if db.postgres:
            schema = schema.replace(
                "INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY"
            ).replace(" COLLATE NOCASE", "")
        db.executescript(schema)
        ensure_column(db, "patients", "user_id", "INTEGER REFERENCES users(id)")
        ensure_column(db, "patients", "city", "TEXT")
        ensure_column(db, "caregivers", "user_id", "INTEGER REFERENCES users(id)")
        ensure_column(db, "caregivers", "city", "TEXT")
        ensure_column(db, "caregivers", "bio", "TEXT")
        ensure_column(db, "caregivers", "license_number", "TEXT")
        ensure_column(db, "bookings", "visit_location", "TEXT NOT NULL DEFAULT ''")
        ensure_column(
            db, "bookings", "health_description", "TEXT NOT NULL DEFAULT ''"
        )
        ensure_column(db, "bookings", "submission_token", "TEXT")
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_care_requests_patient "
            "ON care_requests(patient_id)"
        )
        db.execute(
            "CREATE INDEX IF NOT EXISTS idx_caregivers_availability "
            "ON caregivers(available)"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower "
            "ON users(LOWER(email))"
        )
        db.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_bookings_submission_token "
            "ON bookings(submission_token)"
        )


@app.before_request
def load_request_context():
    global _database_initialized
    if not _database_initialized:
        with _database_initialization_lock:
            if not _database_initialized:
                initialize_database()
                _database_initialized = True

    g.user = None
    user_id = session.get("user_id")
    if user_id is not None:
        with get_db() as db:
            g.user = db.execute(
                "SELECT id, email, role, name, verification_status, review_note "
                "FROM users WHERE id = ?",
                (user_id,),
            ).fetchone()
        if g.user is None:
            session.clear()
    session.setdefault("csrf_token", secrets.token_urlsafe(32))

    if request.method == "POST":
        submitted_token = request.form.get("csrf_token", "")
        if not hmac.compare_digest(submitted_token, session["csrf_token"]):
            abort(400, description="Your form session expired. Please reload and try again.")


def roles_required(*roles):
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if g.user is None:
                return redirect(url_for("login"))
            if g.user["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)

        return wrapped

    return decorator


def storage_configuration():
    supabase_url = os.environ.get("SUPABASE_URL", "").rstrip("/")
    service_role_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY", "")
    if bool(supabase_url) != bool(service_role_key):
        raise RuntimeError(
            "Configure both SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY."
        )
    if os.environ.get("VERCEL") and not (supabase_url and service_role_key):
        raise RuntimeError(
            "Vercel deployment requires Supabase private Storage configuration."
        )
    return supabase_url, service_role_key


@app.route("/style.css")
def stylesheet():
    return send_from_directory(BASE_DIR / "public", "style.css")


@app.route("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(
        BASE_DIR / "public",
        "manifest.webmanifest",
        mimetype="application/manifest+json",
    )


@app.route("/brand-logo")
def brand_logo():
    public_logo = BASE_DIR / "public" / "logo.png"
    project_logo = BASE_DIR / "logo.png"
    if public_logo.is_file():
        return send_from_directory(BASE_DIR / "public", "logo.png")
    if project_logo.is_file():
        return send_from_directory(BASE_DIR, "logo.png")
    return send_from_directory(BASE_DIR / "public", "app-icon.svg")


@app.route("/nutrix-icon-<int:size>.png")
def app_icon(size):
    if size not in {180, 192, 512}:
        abort(404)
    return send_from_directory(
        BASE_DIR / "public",
        f"nutrix-icon-{size}.png",
        mimetype="image/png",
    )


@app.route("/service-worker.js")
def service_worker():
    response = send_from_directory(
        BASE_DIR / "public",
        "service-worker.js",
        mimetype="application/javascript",
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.route("/app.js")
def app_script():
    return send_from_directory(
        BASE_DIR / "public",
        "app.js",
        mimetype="application/javascript",
    )


@app.route("/translations.js")
def translations_script():
    return send_from_directory(
        BASE_DIR / "public",
        "translations.js",
        mimetype="application/javascript",
    )


def storage_request(method, path, body=None, content_type=None):
    supabase_url, service_role_key = storage_configuration()
    if not supabase_url:
        raise RuntimeError("Supabase Storage is not configured.")
    safe_bucket = urllib.parse.quote(STORAGE_BUCKET, safe="")
    safe_path = urllib.parse.quote(path, safe="/")
    endpoint = f"{supabase_url}/storage/v1/object/{safe_bucket}/{safe_path}"
    headers = {
        "apikey": service_role_key,
        "Authorization": f"Bearer {service_role_key}",
    }
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(endpoint, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as response:
            return response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"Supabase Storage {method.lower()} failed ({error.code}): {detail}"
        ) from error
    except urllib.error.URLError as error:
        raise RuntimeError(f"Supabase Storage is unavailable: {error.reason}") from error


def save_credential(upload):
    if upload is None or not upload.filename:
        raise ValueError("Upload an identity or professional credential document.")
    suffix = Path(secure_filename(upload.filename)).suffix.lower()
    if suffix not in ALLOWED_CREDENTIAL_EXTENSIONS:
        raise ValueError("Use a PDF, JPG, JPEG, or PNG document.")
    signature = upload.stream.read(8)
    upload.stream.seek(0)
    valid_signature = (
        (suffix == ".pdf" and signature.startswith(b"%PDF-"))
        or (suffix == ".png" and signature.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and signature.startswith(b"\xff\xd8\xff"))
    )
    if not valid_signature:
        raise ValueError("The uploaded document does not match its file type.")
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    document = upload.read()
    supabase_url, _ = storage_configuration()
    if supabase_url:
        storage_request(
            "POST",
            stored_name,
            document,
            content_type=upload.mimetype or "application/octet-stream",
        )
    else:
        CREDENTIALS_DIR.mkdir(exist_ok=True)
        (CREDENTIALS_DIR / stored_name).write_bytes(document)
    return stored_name


def delete_credential(path):
    if not path:
        return
    if not re.fullmatch(r"[0-9a-f]{32}\.(pdf|jpg|jpeg|png)", path):
        raise ValueError("Invalid stored verification document path.")
    supabase_url, _ = storage_configuration()
    if supabase_url:
        storage_request("DELETE", path)
    else:
        local_path = (CREDENTIALS_DIR / path).resolve()
        if local_path.parent == CREDENTIALS_DIR.resolve():
            local_path.unlink(missing_ok=True)


def read_credential(path):
    if not re.fullmatch(r"[0-9a-f]{32}\.(pdf|jpg|jpeg|png)", path):
        abort(404)
    supabase_url, _ = storage_configuration()
    if supabase_url:
        return storage_request("GET", path)
    local_path = (CREDENTIALS_DIR / path).resolve()
    if local_path.parent != CREDENTIALS_DIR.resolve() or not local_path.is_file():
        abort(404)
    return local_path.read_bytes()


def profile_for_user(db, user):
    table = "patients" if user["role"] == "patient" else "caregivers"
    return db.execute(
        f"SELECT * FROM {table} WHERE user_id = ?",
        (user["id"],),
    ).fetchone()


def verify_registration_fields(email, password, name):
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise ValueError("Enter a valid email address.")
    if not name:
        raise ValueError("Enter your full name.")
    if len(password) < 10:
        raise ValueError("Use a password with at least 10 characters.")


def role_home(role):
    if role == "admin":
        return url_for("dashboard")
    if role == "patient":
        return url_for("patient_dashboard")
    return url_for("caregiver_dashboard")


@app.context_processor
def inject_template_context():
    return {
        "current_user": g.get("user"),
        "csrf_token": session.get("csrf_token", ""),
    }


@app.route("/")
def dashboard():
    if g.user is None:
        return render_template("landing.html")
    if g.user["role"] == "patient":
        return redirect(url_for("patient_dashboard"))
    if g.user["role"] == "caregiver":
        return redirect(url_for("caregiver_dashboard"))
    with get_db() as db:
        patients = db.execute("SELECT COUNT(*) FROM patients").fetchone()[0]
        caregivers = db.execute("SELECT COUNT(*) FROM caregivers").fetchone()[0]
        bookings = db.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        pending = db.execute(
            "SELECT COUNT(*) FROM bookings WHERE status = 'Pending'"
        ).fetchone()[0]
        verification_queue = db.execute(
            "SELECT COUNT(*) FROM users WHERE role != 'admin' "
            "AND verification_status = 'pending'"
        ).fetchone()[0]
    return render_template(
        "dashboard.html",
        patients=patients,
        caregivers=caregivers,
        bookings=bookings,
        pending=pending,
        verification_queue=verification_queue,
    )


@app.route("/register/<role>", methods=["GET", "POST"])
def register(role):
    if role not in {"patient", "caregiver"}:
        abort(404)
    if g.user is not None:
        return redirect(role_home(g.user["role"]))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        name = request.form.get("name", "").strip()
        credential_path = None
        try:
            verify_registration_fields(email, password, name)
            credential_path = save_credential(request.files.get("credential"))
            with get_db() as db:
                if db.execute(
                    "SELECT 1 FROM users WHERE email = ?", (email,)
                ).fetchone():
                    raise ValueError("An account already uses that email address.")

                user_id = db.execute(
                    "INSERT INTO users "
                    "(email, password_hash, role, name, credential_path) "
                    "VALUES (?, ?, ?, ?, ?) RETURNING id",
                    (
                        email,
                        generate_password_hash(password),
                        role,
                        name,
                        credential_path,
                    ),
                ).fetchone()[0]
                if role == "patient":
                    age = request.form.get("age") or None
                    if age is not None and (not age.isdigit() or int(age) > 130):
                        raise ValueError("Enter a valid age between 0 and 130.")
                    db.execute(
                        "INSERT INTO patients "
                        "(user_id, name, age, condition, phone, address, city) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (
                            user_id,
                            name,
                            age,
                            request.form.get("condition", "").strip(),
                            request.form.get("phone", "").strip(),
                            request.form.get("address", "").strip(),
                            request.form.get("city", "").strip(),
                        ),
                    )
                else:
                    caregiver_role = request.form.get("caregiver_role", "").strip()
                    experience = request.form.get("experience", "").strip() or "0"
                    if caregiver_role not in {
                        "Nurse", "Caretaker", "Physiotherapist"
                    }:
                        raise ValueError("Choose a valid care profession.")
                    if not experience.isdigit() or int(experience) > 70:
                        raise ValueError("Enter valid experience from 0 to 70 years.")
                    db.execute(
                        "INSERT INTO caregivers "
                        "(user_id, name, role, phone, experience, available, city, "
                        "bio, license_number) "
                        "VALUES (?, ?, ?, ?, ?, 'Available', ?, ?, ?)",
                        (
                            user_id,
                            name,
                            caregiver_role,
                            request.form.get("phone", "").strip(),
                            int(experience),
                            request.form.get("city", "").strip(),
                            request.form.get("bio", "").strip(),
                            request.form.get("license_number", "").strip(),
                        ),
                    )
            session["user_id"] = user_id
            flash("Your account is ready. Verification is pending admin review.")
            return redirect(role_home(role))
        except ValueError as error:
            if credential_path:
                delete_credential(credential_path)
            flash(str(error), "error")
        except (sqlite3.IntegrityError, psycopg.IntegrityError):
            if credential_path:
                delete_credential(credential_path)
            flash("That email address is already registered.", "error")

    return render_template("register.html", role=role)


@app.route("/login", methods=["GET", "POST"])
def login():
    if g.user is not None:
        return redirect(role_home(g.user["role"]))
    if request.method == "POST":
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        with get_db() as db:
            user = db.execute(
                "SELECT id, email, password_hash, role FROM users WHERE email = ?",
                (email,),
            ).fetchone()
        if user and check_password_hash(user["password_hash"], password):
            session.clear()
            session["user_id"] = user["id"]
            session["csrf_token"] = secrets.token_urlsafe(32)
            return redirect(role_home(user["role"]))
        flash("Email or password is incorrect.", "error")
    return render_template("auth.html")


@app.route("/logout", methods=["POST"])
@roles_required("patient", "caregiver", "admin")
def logout():
    session.clear()
    flash("You have been signed out.")
    return redirect(url_for("dashboard"))


@app.route("/patient")
@roles_required("patient")
def patient_dashboard():
    query = request.args.get("q", "").strip()
    role_filter = request.args.get("role", "").strip()
    with get_db() as db:
        patient = profile_for_user(db, g.user)
        if patient is None:
            abort(404)
        sql = (
            "SELECT caregivers.*, users.verification_status "
            "FROM caregivers JOIN users ON users.id = caregivers.user_id "
            "WHERE users.verification_status = 'approved' "
            "AND caregivers.available = 'Available'"
        )
        params = []
        if query:
            sql += " AND (caregivers.name LIKE ? OR caregivers.role LIKE ? "
            sql += "OR caregivers.city LIKE ?)"
            params.extend([f"%{query}%"] * 3)
        if role_filter in {"Nurse", "Caretaker", "Physiotherapist"}:
            sql += " AND caregivers.role = ?"
            params.append(role_filter)
        caregivers = db.execute(
            sql + " ORDER BY LOWER(caregivers.name)", params
        ).fetchall()
        requests = db.execute(
            "SELECT * FROM care_requests WHERE patient_id = ? "
            "ORDER BY created_at DESC",
            (patient["id"],),
        ).fetchall()
        bookings = db.execute(
            "SELECT bookings.*, caregivers.name AS caregiver_name, "
            "caregivers.role AS caregiver_role "
            "FROM bookings JOIN caregivers ON caregivers.id = bookings.caregiver_id "
            "WHERE bookings.patient_id = ? ORDER BY bookings.id DESC",
            (patient["id"],),
        ).fetchall()
    return render_template(
        "patient_dashboard.html",
        patient=patient,
        caregivers=caregivers,
        requests=requests,
        bookings=bookings,
        query=query,
        role_filter=role_filter,
        booking_tokens={
            caregiver["id"]: secrets.token_urlsafe(32) for caregiver in caregivers
        },
    )


@app.route("/patient/requests", methods=["POST"])
@roles_required("patient")
def create_care_request():
    if g.user["verification_status"] != "approved":
        flash("Your account must be verified before you can post a care request.", "error")
        return redirect(url_for("patient_dashboard"))
    summary = request.form.get("summary", "").strip()
    city = request.form.get("city", "").strip()
    schedule = request.form.get("schedule", "").strip()
    if not summary or not city or not schedule:
        flash("Complete all care request details.", "error")
        return redirect(url_for("patient_dashboard"))
    with get_db() as db:
        patient = profile_for_user(db, g.user)
        if patient is None:
            abort(404)
        db.execute(
            "INSERT INTO care_requests (patient_id, summary, city, schedule) "
            "VALUES (?, ?, ?, ?)",
            (patient["id"], summary, city, schedule),
        )
    flash("Your care request is now visible to verified caregivers.")
    return redirect(url_for("patient_dashboard"))


@app.route("/patient/requests/<int:request_id>/close", methods=["POST"])
@roles_required("patient")
def close_care_request(request_id):
    with get_db() as db:
        patient = profile_for_user(db, g.user)
        changed = db.execute(
            "UPDATE care_requests SET status = 'Closed' "
            "WHERE id = ? AND patient_id = ? AND status = 'Open'",
            (request_id, patient["id"]),
        ).rowcount
    if not changed:
        abort(404)
    flash("Care request closed.")
    return redirect(url_for("patient_dashboard"))


@app.route("/caregiver")
@roles_required("caregiver")
def caregiver_dashboard():
    query = request.args.get("q", "").strip()
    with get_db() as db:
        caregiver = profile_for_user(db, g.user)
        if caregiver is None:
            abort(404)
        requests_sql = (
            "SELECT care_requests.id, care_requests.summary, care_requests.city, "
            "care_requests.schedule, care_requests.created_at "
            "FROM care_requests JOIN patients ON patients.id = care_requests.patient_id "
            "JOIN users ON users.id = patients.user_id "
            "WHERE care_requests.status = 'Open' "
            "AND users.verification_status = 'approved'"
        )
        params = []
        if query:
            requests_sql += (
                " AND (care_requests.summary LIKE ? OR care_requests.city LIKE ?)"
            )
            params.extend([f"%{query}%"] * 2)
        care_requests = db.execute(
            requests_sql + " ORDER BY care_requests.created_at DESC", params
        ).fetchall() if g.user["verification_status"] == "approved" else []
        bookings = db.execute(
            "SELECT bookings.*, patients.name AS patient_name "
            "FROM bookings JOIN patients ON patients.id = bookings.patient_id "
            "WHERE bookings.caregiver_id = ? ORDER BY bookings.id DESC",
            (caregiver["id"],),
        ).fetchall()
    return render_template(
        "caregiver_dashboard.html",
        caregiver=caregiver,
        care_requests=care_requests,
        bookings=bookings,
        query=query,
    )


@app.route("/bookings/<int:booking_id>/status", methods=["POST"])
@roles_required("patient", "caregiver")
def update_my_booking(booking_id):
    status = request.form.get("status", "")
    allowed = {"Cancelled"} if g.user["role"] == "patient" else {
        "Confirmed", "Completed", "Cancelled"
    }
    if status not in allowed:
        abort(400, description="That booking status change is not allowed.")
    with get_db() as db:
        profile = profile_for_user(db, g.user)
        column = "patient_id" if g.user["role"] == "patient" else "caregiver_id"
        changed = db.execute(
            f"UPDATE bookings SET status = ? WHERE id = ? AND {column} = ? "
            "AND status IN ('Pending', 'Confirmed')",
            (status, booking_id, profile["id"]),
        ).rowcount
    if not changed:
        abort(404)
    flash("Booking status updated.")
    return redirect(role_home(g.user["role"]))


@app.route("/bookings/request/<int:caregiver_id>", methods=["POST"])
@roles_required("patient")
def request_booking(caregiver_id):
    if g.user["verification_status"] != "approved":
        flash("Your account must be verified before requesting a booking.", "error")
        return redirect(url_for("patient_dashboard"))
    date = request.form.get("date", "")
    time = request.form.get("time", "")
    submission_token = request.form.get("submission_token", "")
    visit_location = request.form.get("visit_location", "").strip()
    health_description = request.form.get("health_description", "").strip()
    if (
        not submission_token
        or len(submission_token) > 128
        or not date
        or not time
        or not visit_location
        or len(visit_location) > 300
        or not health_description
        or len(health_description) > 1000
    ):
        flash("Please complete all booking fields.", "error")
        return redirect(url_for("patient_dashboard"))
    with get_db() as db:
        patient = profile_for_user(db, g.user)
        caregiver = db.execute(
            "SELECT caregivers.id FROM caregivers "
            "JOIN users ON users.id = caregivers.user_id "
            "WHERE caregivers.id = ? AND caregivers.available = 'Available' "
            "AND users.verification_status = 'approved'",
            (caregiver_id,),
        ).fetchone()
        if caregiver is None:
            abort(404)
        inserted = db.execute(
            "INSERT INTO bookings (patient_id, caregiver_id, date, time, "
            "visit_location, health_description, submission_token) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (submission_token) DO NOTHING",
            (
                patient["id"],
                caregiver_id,
                date,
                time,
                visit_location,
                health_description,
                submission_token,
            ),
        ).rowcount
    if inserted:
        flash("Booking request sent to the caregiver.")
    else:
        flash("This booking request was already submitted.")
    return redirect(url_for("patient_dashboard"))


@app.route("/admin/verifications")
@roles_required("admin")
def admin_verifications():
    status = request.args.get("status", "pending")
    if status not in {"pending", "approved", "rejected"}:
        status = "pending"
    with get_db() as db:
        users = db.execute(
            "SELECT id, name, email, role, verification_status, review_note, created_at "
            "FROM users WHERE role != 'admin' AND verification_status = ? "
            "ORDER BY created_at DESC",
            (status,),
        ).fetchall()
    return render_template("admin_verifications.html", users=users, status=status)


@app.route("/admin/verifications/<int:user_id>/document")
@roles_required("admin")
def verification_document(user_id):
    with get_db() as db:
        user = db.execute(
            "SELECT credential_path FROM users WHERE id = ? AND role != 'admin'",
            (user_id,),
        ).fetchone()
    if user is None or not user["credential_path"]:
        abort(404)
    suffix = Path(user["credential_path"]).suffix.lower()
    return send_file(
        BytesIO(read_credential(user["credential_path"])),
        mimetype=mimetypes.guess_type(f"document{suffix}")[0],
        as_attachment=True,
        download_name=f"verification{suffix}",
    )


@app.route("/admin/verifications/<int:user_id>", methods=["POST"])
@roles_required("admin")
def review_verification(user_id):
    status = request.form.get("status", "")
    if status not in {"approved", "rejected"}:
        abort(400, description="Choose approve or reject.")
    note = request.form.get("review_note", "").strip()
    if status == "rejected" and not note:
        flash("Add a short reason when rejecting a verification.", "error")
        return redirect(url_for("admin_verifications"))
    with get_db() as db:
        changed = db.execute(
            "UPDATE users SET verification_status = ?, review_note = ? "
            "WHERE id = ? AND role != 'admin' AND verification_status = 'pending'",
            (status, note or None, user_id),
        ).rowcount
    if not changed:
        abort(404)
    flash(f"Account verification {status}.")
    return redirect(url_for("admin_verifications"))


@app.route("/verification/resubmit", methods=["POST"])
@roles_required("patient", "caregiver")
def resubmit_verification():
    try:
        credential_path = save_credential(request.files.get("credential"))
    except ValueError as error:
        flash(str(error), "error")
        return redirect(role_home(g.user["role"]))
    with get_db() as db:
        old_path = db.execute(
            "SELECT credential_path FROM users WHERE id = ?", (g.user["id"],)
        ).fetchone()["credential_path"]
        db.execute(
            "UPDATE users SET credential_path = ?, verification_status = 'pending', "
            "review_note = NULL WHERE id = ?",
            (credential_path, g.user["id"]),
        )
    if old_path:
        delete_credential(old_path)
    flash("Your new document was submitted for review.")
    return redirect(role_home(g.user["role"]))


@app.route("/patients", methods=["GET", "POST"])
@roles_required("admin")
def patients_page():
    with get_db() as db:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            if not name:
                flash("Patient name is required.", "error")
                return redirect(url_for("patients_page"))
            db.execute(
                "INSERT INTO patients (name, age, condition, phone, address) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    name,
                    request.form.get("age") or None,
                    request.form.get("condition", "").strip(),
                    request.form.get("phone", "").strip(),
                    request.form.get("address", "").strip(),
                ),
            )
            flash("Patient added successfully.")
            return redirect(url_for("patients_page"))
        patients = db.execute(
            "SELECT * FROM patients ORDER BY id DESC"
        ).fetchall()
    return render_template("patients.html", patients=patients)


@app.route("/caregivers", methods=["GET", "POST"])
@roles_required("admin")
def caregivers_page():
    with get_db() as db:
        if request.method == "POST":
            name = request.form.get("name", "").strip()
            role = request.form.get("role", "").strip()
            if not name or role not in {"Nurse", "Caretaker", "Physiotherapist"}:
                flash("Caregiver name and a valid role are required.", "error")
                return redirect(url_for("caregivers_page"))
            db.execute(
                "INSERT INTO caregivers (name, role, phone, experience, available) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    name,
                    role,
                    request.form.get("phone", "").strip(),
                    request.form.get("experience") or 0,
                    request.form.get("available", "Available"),
                ),
            )
            flash("Caregiver added successfully.")
            return redirect(url_for("caregivers_page"))
        caregivers = db.execute(
            "SELECT * FROM caregivers ORDER BY id DESC"
        ).fetchall()
    return render_template("caregivers.html", caregivers=caregivers)


@app.route("/bookings", methods=["GET", "POST"])
@roles_required("admin")
def bookings_page():
    with get_db() as db:
        if request.method == "POST":
            patient_id = request.form.get("patient_id")
            caregiver_id = request.form.get("caregiver_id")
            date = request.form.get("date")
            time = request.form.get("time")
            submission_token = request.form.get("submission_token", "")
            if (
                not all([patient_id, caregiver_id, date, time])
                or not submission_token
                or len(submission_token) > 128
            ):
                flash("Please complete all booking fields.", "error")
                return redirect(url_for("bookings_page"))
            patient = db.execute(
                "SELECT id FROM patients WHERE id = ?", (patient_id,)
            ).fetchone()
            caregiver = db.execute(
                "SELECT id FROM caregivers WHERE id = ? AND available = 'Available'",
                (caregiver_id,),
            ).fetchone()
            if not patient or not caregiver:
                flash("Choose a valid patient and available caregiver.", "error")
                return redirect(url_for("bookings_page"))
            inserted = db.execute(
                "INSERT INTO bookings (patient_id, caregiver_id, date, time, "
                "submission_token) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT (submission_token) DO NOTHING",
                (patient_id, caregiver_id, date, time, submission_token),
            ).rowcount
            if inserted:
                flash("Booking request created successfully.")
            else:
                flash("This booking request was already submitted.")
            return redirect(url_for("bookings_page"))
        patients = db.execute(
            "SELECT * FROM patients ORDER BY name"
        ).fetchall()
        caregivers = db.execute(
            "SELECT * FROM caregivers WHERE available = 'Available' ORDER BY name"
        ).fetchall()
        bookings = db.execute("""
            SELECT bookings.*, patients.name AS patient_name,
                   caregivers.name AS caregiver_name,
                   caregivers.role AS caregiver_role
            FROM bookings
            JOIN patients ON patients.id = bookings.patient_id
            JOIN caregivers ON caregivers.id = bookings.caregiver_id
            ORDER BY bookings.id DESC
        """).fetchall()
    return render_template(
        "bookings.html",
        patients=patients,
        caregivers=caregivers,
        bookings=bookings,
        submission_token=secrets.token_urlsafe(32),
    )


@app.route("/booking-status/<int:booking_id>", methods=["POST"])
@roles_required("admin")
def booking_status(booking_id):
    status = request.form.get("status")
    if status not in BOOKING_STATUSES:
        abort(400, description="Invalid booking status.")
    with get_db() as db:
        changed = db.execute(
            "UPDATE bookings SET status = ? WHERE id = ?", (status, booking_id)
        ).rowcount
    if not changed:
        abort(404)
    flash("Booking status updated.")
    return redirect(url_for("bookings_page"))


@app.route("/logs", methods=["GET", "POST"])
@roles_required("admin")
def care_logs_page():
    with get_db() as db:
        if request.method == "POST":
            patient_id = request.form.get("patient_id")
            note = request.form.get("note", "").strip()
            patient = db.execute(
                "SELECT id FROM patients WHERE id = ?", (patient_id,)
            ).fetchone() if patient_id else None
            if not patient or not note:
                flash("Choose a patient and enter a care note.", "error")
                return redirect(url_for("care_logs_page"))
            db.execute(
                "INSERT INTO care_logs (patient_id, note) VALUES (?, ?)",
                (patient_id, note),
            )
            flash("Care log saved successfully.")
            return redirect(url_for("care_logs_page"))
        patients = db.execute(
            "SELECT * FROM patients ORDER BY name"
        ).fetchall()
        logs = db.execute("""
            SELECT care_logs.*, patients.name AS patient_name
            FROM care_logs
            JOIN patients ON patients.id = care_logs.patient_id
            ORDER BY care_logs.id DESC
        """).fetchall()
    return render_template("care_logs.html", patients=patients, logs=logs)


@app.cli.command("create-admin")
def create_admin():
    """Create an administrator account for reviewing credential uploads."""
    email = click.prompt("Admin email").strip().lower()
    password = click.prompt("Admin password", hide_input=True, confirmation_prompt=True)
    if not email or "@" not in email or email.startswith("@") or email.endswith("@"):
        raise click.ClickException("Enter a valid email address.")
    if len(password) < 10:
        raise click.ClickException("Admin password must be at least 10 characters.")
    try:
        initialize_database()
        with get_db() as db:
            db.execute(
                "INSERT INTO users (email, password_hash, role, name, "
                "verification_status) VALUES (?, ?, 'admin', ?, 'approved')",
                (email, generate_password_hash(password), email.split("@")[0]),
            )
    except (sqlite3.IntegrityError, psycopg.IntegrityError) as error:
        raise click.ClickException("That email is already registered.") from error
    click.echo(f"Admin account created for {email}.")


if __name__ == "__main__":
    app.run(debug=True)
