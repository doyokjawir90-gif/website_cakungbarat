"""Uji cepat backend. Jalankan:  python smoke_test.py   (memakai database sementara, data asli tidak tersentuh)"""
import io, os, sys, tempfile, struct

tmp = tempfile.mkdtemp()
os.environ["DATA_DIR"] = tmp
os.environ["ADMIN_USERNAME"] = "petugas"
os.environ["ADMIN_PASSWORD"] = "KataSandiAwal-123"
import app as A

c = A.app.test_client()
ok = fail = 0
def check(name, cond):
    global ok, fail
    print(("PASS " if cond else "FAIL ") + name)
    ok += cond; fail += (not cond)

PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64

# --- publik
r = c.get("/api/public"); d = r.get_json()
check("public data 200, layanan awal 4, tanpa berita palsu", r.status_code == 200 and len(d["services"]) == 4 and d["news"] == [])
check("halaman utama & aset statis", c.get("/").status_code == 200 and c.get("/script.js").status_code == 200 and c.get("/images/logo-sicb.png").status_code == 200)
check("file sensitif tidak bisa diakses", all(c.get(p).status_code == 404 for p in ["/app.py", "/../app.py", "/data/cakung_barat.db", "/media/../secret.key", "/api/../app.py"]))
check("header keamanan + CSP", "script-src 'self'" in r.headers["Content-Security-Policy"] and r.headers["X-Content-Type-Options"] == "nosniff")

# --- admin tanpa login ditolak
check("admin API tanpa login = 401", c.get("/api/admin/data").status_code == 401 and c.post("/api/admin/news", data={}).status_code == 401)
check("password lama demo tidak berlaku", c.post("/api/login", json={"username": "admin", "password": "cakung123"}).status_code == 401)

# --- login
r = c.post("/api/login", json={"username": "petugas", "password": "KataSandiAwal-123"}); csrf = r.get_json()["csrf"]
check("login benar 200 + cookie HttpOnly", r.status_code == 200 and "HttpOnly" in r.headers.get("Set-Cookie", ""))
H = {"X-CSRF-Token": csrf}
check("CSRF wajib untuk aksi tulis", c.post("/api/admin/services", json={"name": "x", "summary": "y", "body": "z"}).status_code == 403)

# --- berita + upload
form = {"title": "Kerja Bakti", "category": "Pengumuman", "excerpt": "Ringkas", "body": "Isi lengkap", "emoji": "📢", "published": "1", "photo": (io.BytesIO(PNG), "foto.png")}
r = c.post("/api/admin/news", data=form, headers=H, content_type="multipart/form-data"); n = r.get_json()
check("tambah berita + gambar", r.status_code == 201 and n["mediaUrl"].startswith("/media/") and n["mediaType"] == "image")
check("media bisa diambil publik", c.get(n["mediaUrl"]).status_code == 200)
check("berita terbit tampil publik", len(c.get("/api/public").get_json()["news"]) == 1)
bad = dict(form, photo=(io.BytesIO(b"<?php system($_GET[0]); ?>"), "shell.png"))
check("file palsu (isi bukan gambar) ditolak", c.post("/api/admin/news", data=bad, headers=H, content_type="multipart/form-data").status_code == 400)
svg = dict(form, photo=(io.BytesIO(b"<svg onload=alert(1)>"), "x.svg"))
check("SVG ditolak", c.post("/api/admin/news", data=svg, headers=H, content_type="multipart/form-data").status_code == 400)
r = c.post(f"/api/admin/news/{n['id']}/toggle", headers=H)
check("toggle jadi draf -> hilang dari publik", r.get_json()["published"] is False and c.get("/api/public").get_json()["news"] == [])
old = n["mediaUrl"].split("/")[-1]
r = c.delete(f"/api/admin/news/{n['id']}", headers=H)
check("hapus berita + file media terhapus dari disk", r.status_code == 200 and not os.path.exists(os.path.join(tmp, "uploads", old)))

# --- pengaduan publik
r = c.post("/api/complaints", json={"name": "Budi", "contact": "0812", "category": "Fasilitas umum", "message": "Lampu jalan mati di RT 05"})
t = r.get_json()["ticket"]
check("kirim pengaduan -> tiket", r.status_code == 201 and t.startswith("CB-"))
check("kategori palsu ditolak", c.post("/api/complaints", json={"name": "a", "contact": "b", "category": "X", "message": "pesan panjang sekali"}).status_code == 400)
check("honeypot bot tidak menyimpan", (c.post("/api/complaints", json={"name": "bot", "contact": "b", "category": "Fasilitas umum", "message": "spam spam spam", "website": "http://x"}).status_code == 200))
check("data pribadi tidak bocor di API publik", "Budi" not in c.get("/api/public").get_data(as_text=True))
c.patch(f"/api/admin/complaints/{t}", json={"status": "Diproses"}, headers=H)
pub = c.get("/api/public").get_json()["progress"]["active"]
check("status Diproses tampil publik tanpa nama/pesan", len(pub) == 1 and set(pub[0]) == {"id", "category", "date", "status"})
check("status tidak valid ditolak", c.patch(f"/api/admin/complaints/{t}", json={"status": "Hack"}, headers=H).status_code == 400)
for _ in range(5): c.post("/api/complaints", json={"name": "a", "contact": "b", "category": "Fasilitas umum", "message": "pesan panjang sekali"})
check("rate limit pengaduan (429)", c.post("/api/complaints", json={"name": "a", "contact": "b", "category": "Fasilitas umum", "message": "pesan panjang sekali"}).status_code == 429)

# --- pengaturan + musik
mp3 = b"ID3" + b"\0" * 200
r = c.put("/api/admin/settings", data={"phone": "021-4400000", "whatsapp": "+62 812-3456", "email": "layanan@cakungbarat.go.id", "hours": "Senin-Jumat 08.00-16.00", "address": "Jl. X", "homeTitle": "", "homeText": "", "music": (io.BytesIO(mp3), "lagu.mp3")}, headers=H, content_type="multipart/form-data")
s = r.get_json()
check("simpan pengaturan + musik", r.status_code == 200 and s["musicUrl"].endswith(".mp3") and s["whatsapp"] == "628123456")
check("email tidak valid ditolak", c.put("/api/admin/settings", data={"email": "bukan-email"}, headers=H, content_type="multipart/form-data").status_code == 400)

# --- layanan CRUD
r = c.post("/api/admin/services", json={"name": "Layanan Baru", "summary": "s", "body": "b"}, headers=H); sid = r.get_json()["id"]
check("tambah & hapus layanan", r.status_code == 201 and c.delete(f"/api/admin/services/{sid}", headers=H).status_code == 200)

# --- ganti akun
r = c.post("/api/admin/account", json={"current_password": "salah", "password": "PasswordBaru-456"}, headers=H)
check("ganti password dengan password lama salah ditolak", r.status_code == 400)
check("password pendek ditolak", c.post("/api/admin/account", json={"current_password": "KataSandiAwal-123", "password": "abc"}, headers=H).status_code == 400)
r = c.post("/api/admin/account", json={"current_password": "KataSandiAwal-123", "username": "kasi.pelayanan", "password": "PasswordBaru-456"}, headers=H)
check("ganti username+password", r.status_code == 200 and c.get("/api/admin/data").status_code == 200)
c2 = A.app.test_client()
check("login lama tidak berlaku, baru berlaku", c2.post("/api/login", json={"username": "petugas", "password": "KataSandiAwal-123"}).status_code == 401
      and c2.post("/api/login", json={"username": "kasi.pelayanan", "password": "PasswordBaru-456"}).status_code == 200)
check("login baru bisa mengakses data admin", c2.get("/api/admin/data").status_code == 200)  # c2 login setelah ganti
c3 = A.app.test_client(); c3.post("/api/login", json={"username": "kasi.pelayanan", "password": "PasswordBaru-456"})
c3.post("/api/admin/account", json={"current_password": "PasswordBaru-456", "password": "LagiBaru-789"}, headers={"X-CSRF-Token": c3.get("/api/me").get_json()["csrf"]})
check("sesi c2 mati setelah c3 ganti password", c2.get("/api/admin/data").status_code == 401)

# --- brute force
c4 = A.app.test_client()
codes = [c4.post("/api/login", json={"username": "kasi.pelayanan", "password": "tebak%d" % i}).status_code for i in range(7)]
check("brute force dikunci (429) setelah 5 gagal", codes[:5] == [401] * 5 and codes[5] == 429)
check("logout", c.post("/api/logout").status_code == 200 and c.get("/api/admin/data").status_code == 401)

print(f"\n{ok} lulus, {fail} gagal"); sys.exit(1 if fail else 0)
