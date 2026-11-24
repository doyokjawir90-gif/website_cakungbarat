"""
Portal Informasi Kelurahan Cakung Barat - server produksi.

Flask + SQLite. Semua data (berita, layanan, pengaduan, pengaturan, akun admin)
disimpan di server, bukan di browser. Login admin diverifikasi di server dengan
password ter-hash, sesi cookie HttpOnly, proteksi CSRF, dan pembatasan percobaan.

Jalankan produksi:  gunicorn -w 3 -b 127.0.0.1:8000 app:app
Jalankan lokal:     python app.py
"""
import functools
import hmac
import logging
import os
import re
import secrets
import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from flask import Flask, abort, g, jsonify, request, send_from_directory, session
from werkzeug.exceptions import HTTPException, RequestEntityTooLarge
from werkzeug.middleware.proxy_fix import ProxyFix
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = Path(__file__).resolve().parent


def _load_dotenv():
    """Baca file .env sederhana (KEY=VALUE) bila ada. Variabel OS tetap diprioritaskan."""
    env_file = BASE_DIR / ".env"
    if not env_file.is_file():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

STATIC_DIR = BASE_DIR / "static"
DATA_DIR = Path(os.environ.get("DATA_DIR", BASE_DIR / "data")).resolve()
UPLOAD_DIR = DATA_DIR / "uploads"
DB_PATH = DATA_DIR / "cakung_barat.db"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)

COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"
WIB = timezone(timedelta(hours=7))

NEWS_CATEGORIES = ("Pengumuman", "Kegiatan", "Layanan", "Informasi Wilayah")
COMPLAINT_CATEGORIES = (
    "Fasilitas umum",
    "Kebersihan lingkungan",
    "Administrasi pelayanan",
    "Keamanan dan ketertiban",
    "Saran dan lainnya",
)
COMPLAINT_STATUSES = ("Baru", "Diproses", "Selesai")
SETTING_KEYS = ("phone", "whatsapp", "email", "hours", "address", "homeTitle", "homeText")

MAX_IMAGE = 20 * 1024 * 1024
MAX_VIDEO = 100 * 1024 * 1024
MAX_AUDIO = 15 * 1024 * 1024


def _load_secret_key():
    env_key = os.environ.get("SECRET_KEY")
    if env_key:
        return env_key
    path = DATA_DIR / "secret.key"
    key = secrets.token_hex(32)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            fh.write(key)
        return key
    except FileExistsError:
        for _ in range(30):  # worker lain mungkin sedang menulis file
            text = path.read_text().strip()
            if text:
                return text
            time.sleep(0.1)
        raise


app = Flask(__name__, static_folder=None)
app.config.update(
    SECRET_KEY=_load_secret_key(),
    MAX_CONTENT_LENGTH=MAX_VIDEO + 5 * 1024 * 1024,
    SESSION_COOKIE_NAME="cb_session",
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=COOKIE_SECURE,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
    JSON_AS_ASCII=False,
)
if os.environ.get("BEHIND_PROXY", "0") == "1":
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("cakung-barat")

# Hash palsu agar waktu respons login sama baik username ada maupun tidak.
_DUMMY_HASH = generate_password_hash(secrets.token_hex(8))


# ----------------------------------------------------------------------------
# Database
# ----------------------------------------------------------------------------
SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE COLLATE NOCASE,
  password_hash TEXT NOT NULL,
  pw_version INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS news(
  id TEXT PRIMARY KEY,
  title TEXT NOT NULL,
  category TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  body TEXT NOT NULL,
  emoji TEXT,
  published INTEGER NOT NULL DEFAULT 1,
  date TEXT NOT NULL,
  media_file TEXT,
  media_type TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS complaints(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  contact TEXT NOT NULL,
  category TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'Baru',
  date TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS services(
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  summary TEXT NOT NULL,
  body TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS rate_events(
  id INTEGER PRIMARY KEY,
  key TEXT NOT NULL,
  ts REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_rate ON rate_events(key, ts);
CREATE INDEX IF NOT EXISTS idx_news_date ON news(published, date);
CREATE INDEX IF NOT EXISTS idx_complaints_status ON complaints(status, date);
"""

DEFAULT_SERVICES = [
    ("srv1", "Surat Pengantar",
     "Informasi pengantar administrasi kependudukan dan keperluan lainnya.",
     "Siapkan dokumen identitas dan dokumen pendukung sesuai keperluan. Persyaratan, alur, dan jadwal dapat berbeda, "
     "sehingga warga disarankan mengonfirmasi ketentuan terbaru kepada petugas kelurahan melalui kanal resmi sebelum berkunjung."),
    ("srv2", "Administrasi Kependudukan",
     "Panduan awal untuk dokumen dan perubahan data kependudukan.",
     "Layanan administrasi kependudukan mencakup pengurusan dokumen dan perubahan data kependudukan. Siapkan dokumen "
     "identitas serta dokumen pendukung yang relevan. Konfirmasikan persyaratan sesuai jenis pengurusan kepada petugas atau instansi berwenang."),
    ("srv3", "Surat Keterangan",
     "Informasi umum layanan surat keterangan sesuai kebutuhan warga.",
     "Surat keterangan diterbitkan sesuai jenis keperluan dan ketentuan yang berlaku. Sampaikan tujuan pengurusan kepada "
     "petugas dan siapkan identitas serta bukti pendukung. Persyaratan akhir perlu dikonfirmasi melalui kanal resmi kelurahan."),
    ("srv4", "Informasi Domisili",
     "Informasi terkait domisili dan administrasi lingkungan.",
     "Informasi domisili berkaitan dengan keterangan tempat tinggal dan administrasi lingkungan. Siapkan identitas serta "
     "dokumen pendukung alamat tempat tinggal. Untuk ketentuan dan prosedur terbaru, silakan menghubungi petugas kelurahan "
     "atau pengurus RT/RW setempat."),
]


def now_iso():
    return datetime.now(WIB).isoformat(timespec="seconds")


def today_str():
    return datetime.now(WIB).strftime("%Y-%m-%d")


def connect():
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = connect()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        # Isi panduan layanan awal hanya sekali (tidak diisi ulang bila admin sengaja menghapusnya).
        if not conn.execute("SELECT 1 FROM settings WHERE key='servicesSeeded'").fetchone():
            conn.executemany(
                "INSERT OR IGNORE INTO services(id,name,summary,body,position) VALUES(?,?,?,?,?)",
                [(sid, name, summary, body, pos) for pos, (sid, name, summary, body) in enumerate(DEFAULT_SERVICES)],
            )
            conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('servicesSeeded','1')")
        conn.commit()

        # Akun admin awal: dari variabel lingkungan, atau password acak yang dicetak sekali.
        username = os.environ.get("ADMIN_USERNAME", "admin").strip() or "admin"
        env_password = os.environ.get("ADMIN_PASSWORD", "")
        password = env_password or secrets.token_urlsafe(12)
        cur = conn.execute(
            "INSERT INTO users(username,password_hash,created_at) "
            "SELECT ?,?,? WHERE NOT EXISTS (SELECT 1 FROM users)",
            (username, generate_password_hash(password), now_iso()),
        )
        conn.commit()
        if cur.rowcount == 1 and not env_password:
            note = DATA_DIR / "AKUN_ADMIN_AWAL.txt"
            note.write_text(
                f"Username: {username}\nPassword: {password}\n\n"
                "Segera masuk, ganti password di Dashboard > Pengaturan Portal > Akun admin,\n"
                "lalu HAPUS file ini.\n",
                encoding="utf-8",
            )
            os.chmod(note, 0o600)
            log.warning("Akun admin awal dibuat. Username=%s Password=%s (juga tersimpan di %s)", username, password, note)
        elif cur.rowcount == 1:
            log.info("Akun admin awal dibuat dari ADMIN_USERNAME/ADMIN_PASSWORD (username=%s).", username)
    finally:
        conn.close()


def db():
    if "db" not in g:
        g.db = connect()
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


# ----------------------------------------------------------------------------
# Utilitas
# ----------------------------------------------------------------------------
class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


@app.errorhandler(ApiError)
def handle_api_error(exc):
    return jsonify(error=exc.message), exc.status


@app.errorhandler(RequestEntityTooLarge)
def handle_too_large(_exc):
    return jsonify(error="Ukuran file terlalu besar."), 413


@app.errorhandler(HTTPException)
def handle_http(exc):
    if request.path.startswith("/api/"):
        return jsonify(error=exc.description or exc.name), exc.code
    return exc


@app.errorhandler(Exception)
def handle_unexpected(exc):
    log.exception("Kesalahan tak terduga")
    if request.path.startswith("/api/"):
        return jsonify(error="Terjadi kesalahan pada server."), 500
    return "Terjadi kesalahan pada server.", 500


_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean(value, maxlen, required=False, label="Data"):
    text = _CTRL.sub("", str(value if value is not None else "")).strip()
    if required and not text:
        raise ApiError(f"{label} wajib diisi.")
    if len(text) > maxlen:
        raise ApiError(f"{label} maksimal {maxlen} karakter.")
    return text


def client_ip():
    return request.remote_addr or "unknown"


def rate_count(key, window):
    return db().execute(
        "SELECT COUNT(*) FROM rate_events WHERE key=? AND ts>?", (key, time.time() - window)
    ).fetchone()[0]


def rate_add(key):
    conn = db()
    conn.execute("INSERT INTO rate_events(key,ts) VALUES(?,?)", (key, time.time()))
    if secrets.randbelow(50) == 0:
        conn.execute("DELETE FROM rate_events WHERE ts<?", (time.time() - 86400,))
    conn.commit()


def rate_clear(key):
    db().execute("DELETE FROM rate_events WHERE key=?", (key,))
    db().commit()


def get_json():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        raise ApiError("Permintaan tidak valid.")
    return data


# ----------------------------------------------------------------------------
# Unggah file: validasi isi file (bukan hanya ekstensi)
# ----------------------------------------------------------------------------
def _detect(head):
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "image", "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "webp"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "wav"
    if head[:4] == b"OggS":
        return "audio", "ogg"
    if head.startswith(b"ID3") or (len(head) > 1 and head[0] == 0xFF and (head[1] & 0xE0) == 0xE0):
        return "audio", "mp3"
    if head.startswith(b"\x1a\x45\xdf\xa3"):
        return "video", "webm"
    if head[4:8] == b"ftyp":
        return "ftyp", None
    return None, None


def save_upload(file_storage, kind, max_bytes, label):
    stream = file_storage.stream
    stream.seek(0, os.SEEK_END)
    size = stream.tell()
    stream.seek(0)
    if size == 0:
        raise ApiError(f"File {label} kosong.")
    if size > max_bytes:
        raise ApiError(f"Ukuran {label} melebihi batas {max_bytes // (1024 * 1024)} MB.")
    head = stream.read(16)
    stream.seek(0)
    found_kind, ext = _detect(head)
    if found_kind == "ftyp":
        found_kind, ext = (kind, "mp4" if kind == "video" else "m4a") if kind in ("video", "audio") else (None, None)
    if found_kind != kind or not ext:
        raise ApiError(f"Format {label} tidak didukung.")
    name = f"{uuid.uuid4().hex}.{ext}"
    file_storage.save(UPLOAD_DIR / name)
    return name


_MEDIA_NAME = re.compile(r"^[0-9a-f]{32}\.(jpg|png|gif|webp|mp4|webm|mp3|wav|ogg|m4a)$")


def remove_media(name):
    if name and _MEDIA_NAME.match(name):
        try:
            (UPLOAD_DIR / name).unlink()
        except FileNotFoundError:
            pass


# ----------------------------------------------------------------------------
# Serialisasi
# ----------------------------------------------------------------------------
def news_json(r):
    return {
        "id": r["id"],
        "title": r["title"],
        "category": r["category"],
        "date": r["date"],
        "excerpt": r["excerpt"],
        "body": r["body"],
        "emoji": r["emoji"] or "",
        "published": bool(r["published"]),
        "mediaUrl": f"/media/{r['media_file']}" if r["media_file"] else "",
        "mediaType": r["media_type"] or "",
    }


def service_json(r):
    return {"id": r["id"], "name": r["name"], "summary": r["summary"], "body": r["body"]}


def complaint_json(r):
    return {
        "id": r["id"], "name": r["name"], "contact": r["contact"], "category": r["category"],
        "message": r["message"], "status": r["status"], "date": r["date"],
    }


def get_settings():
    stored = {r["key"]: r["value"] for r in db().execute("SELECT key,value FROM settings")}
    out = {k: stored.get(k, "") for k in SETTING_KEYS}
    out["musicUrl"] = f"/media/{stored['musicFile']}" if stored.get("musicFile") else ""
    return out


def set_setting(key, value):
    db().execute(
        "INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def all_news(published_only):
    sql = "SELECT * FROM news"
    if published_only:
        sql += " WHERE published=1"
    sql += " ORDER BY date DESC, created_at DESC"
    return [news_json(r) for r in db().execute(sql)]


def all_services():
    return [service_json(r) for r in db().execute("SELECT * FROM services ORDER BY position, rowid")]


# ----------------------------------------------------------------------------
# Autentikasi
# ----------------------------------------------------------------------------
def admin_required(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        uid = session.get("uid")
        user = db().execute("SELECT id,username,pw_version FROM users WHERE id=?", (uid,)).fetchone() if uid else None
        if not user or user["pw_version"] != session.get("pwv"):
            session.clear()
            raise ApiError("Sesi berakhir. Silakan masuk kembali.", 401)
        if request.method not in ("GET", "HEAD", "OPTIONS"):
            token = request.headers.get("X-CSRF-Token", "")
            if not token or not hmac.compare_digest(token, session.get("csrf", "")):
                raise ApiError("Token keamanan tidak valid. Muat ulang halaman lalu coba lagi.", 403)
        g.user = user
        return fn(*args, **kwargs)

    return wrapper


@app.post("/api/login")
def login():
    data = get_json()
    username = str(data.get("username", "")).strip()[:60]
    password = str(data.get("password", ""))[:200]
    ip = client_ip()
    user_key = f"login:user:{username.lower()}"
    ip_key = f"login:ip:{ip}"
    if rate_count(ip_key, 900) >= 10 or rate_count(user_key, 900) >= 5:
        raise ApiError("Terlalu banyak percobaan masuk. Coba lagi dalam 15 menit.", 429)
    user = db().execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
    valid = check_password_hash(user["password_hash"] if user else _DUMMY_HASH, password)
    if not (user and valid):
        rate_add(ip_key)
        rate_add(user_key)
        log.warning("Login gagal untuk '%s' dari %s", username, ip)
        raise ApiError("Username atau password salah.", 401)
    rate_clear(user_key)
    session.clear()
    session.permanent = True
    session["uid"] = user["id"]
    session["pwv"] = user["pw_version"]
    session["csrf"] = secrets.token_urlsafe(32)
    log.info("Admin '%s' masuk dari %s", user["username"], ip)
    return jsonify(ok=True, username=user["username"], csrf=session["csrf"])


@app.post("/api/logout")
def logout():
    session.clear()
    return jsonify(ok=True)


@app.get("/api/me")
def me():
    uid = session.get("uid")
    user = db().execute("SELECT username,pw_version FROM users WHERE id=?", (uid,)).fetchone() if uid else None
    if not user or user["pw_version"] != session.get("pwv"):
        return jsonify(authenticated=False)
    return jsonify(authenticated=True, username=user["username"], csrf=session.get("csrf", ""))


# ----------------------------------------------------------------------------
# API publik
# ----------------------------------------------------------------------------
@app.get("/api/public")
def public_data():
    def progress(status):
        rows = db().execute(
            "SELECT id,category,date,status FROM complaints WHERE status=? "
            "ORDER BY date DESC, created_at DESC LIMIT 6", (status,)
        )
        return [dict(r) for r in rows]

    return jsonify(
        news=all_news(True),
        services=all_services(),
        settings=get_settings(),
        progress={"active": progress("Diproses"), "completed": progress("Selesai")},
    )


@app.post("/api/complaints")
def create_complaint():
    data = get_json()
    if str(data.get("website", "")).strip():  # kolom jebakan bot: pura-pura berhasil
        return jsonify(ok=True, ticket="CB-" + str(datetime.now(WIB).year) + "000000")
    ip = client_ip()
    key = f"complaint:{ip}"
    if rate_count(key, 3600) >= 5:
        raise ApiError("Terlalu banyak pengaduan dari perangkat ini. Silakan coba lagi nanti.", 429)
    name = clean(data.get("name"), 80, True, "Nama")
    contact = clean(data.get("contact"), 100, True, "Kontak")
    category = str(data.get("category", ""))
    if category not in COMPLAINT_CATEGORIES:
        raise ApiError("Pilih kategori pengaduan yang valid.")
    message = clean(data.get("message"), 1200, True, "Isi pengaduan")
    if len(message) < 10:
        raise ApiError("Isi pengaduan terlalu singkat. Jelaskan dengan lebih rinci.")
    conn = db()
    for _ in range(10):
        ticket = f"CB-{datetime.now(WIB).year}{secrets.randbelow(10**6):06d}"
        try:
            conn.execute(
                "INSERT INTO complaints(id,name,contact,category,message,status,date,created_at) "
                "VALUES(?,?,?,?,?,'Baru',?,?)",
                (ticket, name, contact, category, message, today_str(), now_iso()),
            )
            conn.commit()
            break
        except sqlite3.IntegrityError:
            continue
    else:
        raise ApiError("Gagal membuat nomor tiket. Coba lagi.", 500)
    rate_add(key)
    return jsonify(ok=True, ticket=ticket), 201


# ----------------------------------------------------------------------------
# API admin
# ----------------------------------------------------------------------------
@app.get("/api/admin/data")
@admin_required
def admin_data():
    complaints = [
        complaint_json(r) for r in db().execute("SELECT * FROM complaints ORDER BY date DESC, created_at DESC")
    ]
    return jsonify(news=all_news(False), complaints=complaints, services=all_services(), settings=get_settings())


def _news_fields():
    f = request.form
    category = f.get("category", "")
    if category not in NEWS_CATEGORIES:
        raise ApiError("Kategori tidak valid.")
    return {
        "title": clean(f.get("title"), 120, True, "Judul"),
        "category": category,
        "excerpt": clean(f.get("excerpt"), 240, True, "Ringkasan"),
        "body": clean(f.get("body"), 5000, True, "Isi lengkap"),
        "emoji": clean(f.get("emoji"), 16, False, "Emoji") or "📌",
        "published": 1 if f.get("published") == "1" else 0,
    }


def _upload_from_request():
    photo, video = request.files.get("photo"), request.files.get("video")
    has_photo, has_video = bool(photo and photo.filename), bool(video and video.filename)
    if has_photo and has_video:
        raise ApiError("Pilih gambar atau video saja untuk satu berita.")
    if has_photo:
        return photo, "image", MAX_IMAGE, "gambar"
    if has_video:
        return video, "video", MAX_VIDEO, "video"
    return None


@app.post("/api/admin/news")
@admin_required
def news_create():
    fields = _news_fields()
    upload = _upload_from_request()
    media_file = media_type = None
    if upload:
        media_file = save_upload(*upload)
        media_type = upload[1]
    nid = "n" + uuid.uuid4().hex[:12]
    try:
        db().execute(
            "INSERT INTO news(id,title,category,excerpt,body,emoji,published,date,media_file,media_type,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (nid, fields["title"], fields["category"], fields["excerpt"], fields["body"], fields["emoji"],
             fields["published"], today_str(), media_file, media_type, now_iso(), now_iso()),
        )
        db().commit()
    except Exception:
        remove_media(media_file)
        raise
    return jsonify(news_json(db().execute("SELECT * FROM news WHERE id=?", (nid,)).fetchone())), 201


@app.put("/api/admin/news/<nid>")
@admin_required
def news_update(nid):
    row = db().execute("SELECT * FROM news WHERE id=?", (nid,)).fetchone()
    if not row:
        raise ApiError("Informasi tidak ditemukan.", 404)
    fields = _news_fields()
    upload = _upload_from_request()
    media_file, media_type = row["media_file"], row["media_type"]
    old_to_delete = None
    new_file = None
    if upload:
        new_file = save_upload(*upload)
        old_to_delete = media_file
        media_file, media_type = new_file, upload[1]
    elif request.form.get("remove_media") == "1":
        old_to_delete = media_file
        media_file = media_type = None
    try:
        db().execute(
            "UPDATE news SET title=?,category=?,excerpt=?,body=?,emoji=?,published=?,media_file=?,media_type=?,updated_at=? WHERE id=?",
            (fields["title"], fields["category"], fields["excerpt"], fields["body"], fields["emoji"],
             fields["published"], media_file, media_type, now_iso(), nid),
        )
        db().commit()
    except Exception:
        remove_media(new_file)
        raise
    remove_media(old_to_delete)
    return jsonify(news_json(db().execute("SELECT * FROM news WHERE id=?", (nid,)).fetchone()))


@app.post("/api/admin/news/<nid>/toggle")
@admin_required
def news_toggle(nid):
    cur = db().execute(
        "UPDATE news SET published=1-published, updated_at=? WHERE id=?", (now_iso(), nid)
    )
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Informasi tidak ditemukan.", 404)
    return jsonify(news_json(db().execute("SELECT * FROM news WHERE id=?", (nid,)).fetchone()))


@app.delete("/api/admin/news/<nid>")
@admin_required
def news_delete(nid):
    row = db().execute("SELECT media_file FROM news WHERE id=?", (nid,)).fetchone()
    if not row:
        raise ApiError("Informasi tidak ditemukan.", 404)
    db().execute("DELETE FROM news WHERE id=?", (nid,))
    db().commit()
    remove_media(row["media_file"])
    return jsonify(ok=True)


@app.patch("/api/admin/complaints/<cid>")
@admin_required
def complaint_status(cid):
    status = str(get_json().get("status", ""))
    if status not in COMPLAINT_STATUSES:
        raise ApiError("Status tidak valid.")
    cur = db().execute("UPDATE complaints SET status=? WHERE id=?", (status, cid))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Pengaduan tidak ditemukan.", 404)
    return jsonify(ok=True)


@app.delete("/api/admin/complaints/<cid>")
@admin_required
def complaint_delete(cid):
    cur = db().execute("DELETE FROM complaints WHERE id=?", (cid,))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Pengaduan tidak ditemukan.", 404)
    return jsonify(ok=True)


def _service_fields():
    d = get_json()
    return (
        clean(d.get("name"), 100, True, "Nama layanan"),
        clean(d.get("summary"), 300, True, "Ringkasan"),
        clean(d.get("body"), 5000, True, "Panduan"),
    )


@app.post("/api/admin/services")
@admin_required
def service_create():
    name, summary, body = _service_fields()
    sid = "srv" + uuid.uuid4().hex[:10]
    pos = db().execute("SELECT COALESCE(MAX(position),0)+1 FROM services").fetchone()[0]
    db().execute("INSERT INTO services(id,name,summary,body,position) VALUES(?,?,?,?,?)", (sid, name, summary, body, pos))
    db().commit()
    return jsonify(service_json(db().execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone())), 201


@app.put("/api/admin/services/<sid>")
@admin_required
def service_update(sid):
    name, summary, body = _service_fields()
    cur = db().execute("UPDATE services SET name=?,summary=?,body=? WHERE id=?", (name, summary, body, sid))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Layanan tidak ditemukan.", 404)
    return jsonify(service_json(db().execute("SELECT * FROM services WHERE id=?", (sid,)).fetchone()))


@app.delete("/api/admin/services/<sid>")
@admin_required
def service_delete(sid):
    cur = db().execute("DELETE FROM services WHERE id=?", (sid,))
    db().commit()
    if cur.rowcount == 0:
        raise ApiError("Layanan tidak ditemukan.", 404)
    return jsonify(ok=True)


_PHONE_RE = re.compile(r"^[0-9+()\-.\s]{0,40}$")
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@app.put("/api/admin/settings")
@admin_required
def settings_update():
    f = request.form
    phone = clean(f.get("phone"), 40, False, "Nomor telepon")
    if not _PHONE_RE.match(phone):
        raise ApiError("Nomor telepon hanya boleh berisi angka dan tanda + ( ) - .")
    whatsapp = re.sub(r"[^\d]", "", clean(f.get("whatsapp"), 30, False, "Nomor WhatsApp"))
    email = clean(f.get("email"), 120, False, "Email")
    if email and not _EMAIL_RE.match(email):
        raise ApiError("Format email tidak valid.")
    values = {
        "phone": phone,
        "whatsapp": whatsapp,
        "email": email,
        "hours": clean(f.get("hours"), 160, False, "Jam pelayanan"),
        "address": clean(f.get("address"), 300, False, "Alamat"),
        "homeTitle": clean(f.get("homeTitle"), 120, False, "Judul Beranda"),
        "homeText": clean(f.get("homeText"), 500, False, "Deskripsi Beranda"),
    }
    music = request.files.get("music")
    new_music = None
    if music and music.filename:
        new_music = save_upload(music, "audio", MAX_AUDIO, "musik")
    old_music = db().execute("SELECT value FROM settings WHERE key='musicFile'").fetchone()
    old_music = old_music["value"] if old_music else None
    try:
        for key, value in values.items():
            set_setting(key, value)
        if new_music:
            set_setting("musicFile", new_music)
        elif f.get("remove_music") == "1":
            db().execute("DELETE FROM settings WHERE key='musicFile'")
        db().commit()
    except Exception:
        remove_media(new_music)
        raise
    if (new_music or f.get("remove_music") == "1") and old_music:
        remove_media(old_music)
    return jsonify(get_settings())


_USERNAME_RE = re.compile(r"^[A-Za-z0-9._-]{3,40}$")


@app.post("/api/admin/account")
@admin_required
def account_update():
    d = get_json()
    key = f"account:{g.user['id']}"
    if rate_count(key, 900) >= 5:
        raise ApiError("Terlalu banyak percobaan. Coba lagi dalam 15 menit.", 429)
    row = db().execute("SELECT * FROM users WHERE id=?", (g.user["id"],)).fetchone()
    if not check_password_hash(row["password_hash"], str(d.get("current_password", ""))):
        rate_add(key)
        raise ApiError("Password saat ini tidak sesuai. Perubahan tidak disimpan.", 400)
    new_username = clean(d.get("username"), 40, False, "Username")
    new_password = str(d.get("password") or "")
    if not new_username and not new_password:
        raise ApiError("Masukkan username baru atau password baru terlebih dahulu.")
    if new_username and not _USERNAME_RE.match(new_username):
        raise ApiError("Username 3–40 karakter: huruf, angka, titik, garis bawah, atau tanda hubung.")
    if new_password:
        if len(new_password) < 8:
            raise ApiError("Password baru minimal 8 karakter.")
        if len(new_password) > 128:
            raise ApiError("Password baru maksimal 128 karakter.")
        if new_password.lower() == (new_username or row["username"]).lower():
            raise ApiError("Password tidak boleh sama dengan username.")
    username = new_username or row["username"]
    pw_hash, version = row["password_hash"], row["pw_version"]
    if new_password:
        pw_hash, version = generate_password_hash(new_password), version + 1
    try:
        db().execute("UPDATE users SET username=?,password_hash=?,pw_version=? WHERE id=?", (username, pw_hash, version, row["id"]))
        db().commit()
    except sqlite3.IntegrityError:
        raise ApiError("Username sudah digunakan.")
    session["pwv"] = version  # sesi ini tetap valid; sesi lain otomatis keluar
    return jsonify(ok=True, username=username)


# ----------------------------------------------------------------------------
# File media & halaman statis
# ----------------------------------------------------------------------------
@app.get("/media/<name>")
def media(name):
    if not _MEDIA_NAME.match(name):
        abort(404)
    resp = send_from_directory(UPLOAD_DIR, name, conditional=True, max_age=60 * 60 * 24 * 30)
    resp.headers["Content-Disposition"] = "inline"
    return resp


@app.get("/healthz")
def healthz():
    db().execute("SELECT 1")
    return jsonify(status="ok")


@app.get("/")
def index():
    return _serve_static("index.html")


@app.get("/<path:name>")
def static_files(name):
    return _serve_static(name)


def _serve_static(name):
    if name.startswith(("api/", "media/")):
        abort(404)
    full = (STATIC_DIR / name).resolve()
    if STATIC_DIR not in full.parents or not full.is_file():
        abort(404)
    resp = send_from_directory(STATIC_DIR, name, conditional=True)
    if name.endswith((".html", ".css", ".js")):
        resp.headers["Cache-Control"] = "no-cache"
    else:
        resp.headers["Cache-Control"] = "public, max-age=86400"
    return resp


@app.after_request
def security_headers(resp):
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    resp.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; img-src 'self' data:; media-src 'self' https://ncltr.info; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'; object-src 'none'",
    )
    if COOKIE_SECURE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    if request.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


init_db()

if __name__ == "__main__":
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    log.info("Mode pengembangan di http://%s:%s - untuk produksi gunakan gunicorn (lihat README).", host, port)
    app.run(host=host, port=port, debug=False, threaded=True)
