# Portal Informasi Kelurahan Cakung Barat — Versi Produksi

Versi ini sudah bukan demo. Semua data (berita, layanan, pengaduan, pengaturan, akun admin, foto/video/musik)
disimpan **di server**, sehingga tampil sama di semua perangkat dan tidak hilang saat browser dibersihkan.

## Apa yang berubah dari versi demo

| Versi demo | Versi produksi |
|---|---|
| Data di `localStorage` browser | Database SQLite di server (`data/cakung_barat.db`) |
| Login `admin` / `cakung123` dicek di browser (bisa dibobol lewat DevTools) | Login dicek di server, password ter-hash, sesi cookie HttpOnly, proteksi CSRF, penguncian 15 menit setelah 5 kali salah |
| Foto/video di IndexedDB browser | File disimpan di server (`data/uploads`), isi file divalidasi (bukan sekadar ekstensi) |
| Admin lain tidak melihat perubahan | Semua perubahan langsung tampil ke semua pengunjung |
| Pengaduan hanya ada di satu browser | Pengaduan warga masuk ke server dan muncul di dashboard admin |
| Berita contoh & teks "demo/prototipe" | Dihapus; berita dimulai kosong (isi sendiri lewat dashboard) |

Perubahan perilaku yang disengaja:
- **Privasi pengaduan:** bagian "Proses Pengaduan Warga" di halaman publik kini hanya menampilkan nomor tiket, kategori, dan status.
  Isi pengaduan dan nama pelapor hanya terlihat oleh admin.
- **Password minimal 8 karakter**, dan mengganti password otomatis mengeluarkan sesi login di perangkat lain.
- Tidak ada password bawaan. Password awal dibuat saat pertama dijalankan (lihat di bawah).

## Isi paket

```
app.py              server (Flask)
static/             tampilan website (index.html, style.css, script.js, images/)
requirements.txt    dependensi Python
.env.example        contoh konfigurasi
Dockerfile, docker-compose.yml
deploy/             contoh Nginx dan systemd
backup.sh           cadangan database + unggahan
smoke_test.py       uji otomatis backend (33 pemeriksaan)
```

## Cara tercepat agar bisa dipakai semua orang

**Opsi A — VPS sendiri (satu perintah).** Sewa VPS Ubuntu (sekitar 1 vCPU / 1 GB RAM sudah cukup), arahkan domain
(A record) ke IP server, unggah folder ini ke server, lalu:

```bash
sudo bash install.sh domain-anda.go.id email-anda@contoh.com
```

Skrip memasang semuanya (aplikasi, Nginx, HTTPS gratis Let's Encrypt, cadangan harian) dan menampilkan password admin awal.

**Opsi B — tanpa mengurus server.** Gunakan `render.yaml` di Render.com (paket berbayar, karena data butuh disk permanen).
Isi `ADMIN_PASSWORD` di dashboard Render. Hosting gratis yang tidak punya disk permanen tidak cocok: data dan unggahan akan hilang saat restart.

Detail langkah manual ada di bagian bawah.

## Coba di komputer sendiri

Butuh Python 3.10+.

```bash
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Buka http://127.0.0.1:8000. Password admin awal dicetak di terminal dan disimpan di `data/AKUN_ADMIN_AWAL.txt`
(atau tentukan sendiri lewat `ADMIN_USERNAME` / `ADMIN_PASSWORD`). Setelah masuk, **ganti password di
Dashboard → Pengaturan Portal → Akun admin, lalu hapus file `AKUN_ADMIN_AWAL.txt`.**

## Pasang di server (VPS Ubuntu) — jalur yang disarankan

Anda perlu: server/VPS yang mendukung Python (bukan hosting PHP biasa), dan sebuah domain yang diarahkan ke IP server.

```bash
# 1. Pasang kebutuhan
sudo apt update && sudo apt install -y python3-venv nginx certbot python3-certbot-nginx

# 2. Salin proyek ke /opt/cakung-barat, lalu:
sudo useradd -r -m -s /usr/sbin/nologin cakung
cd /opt/cakung-barat
python3 -m venv venv && ./venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env          # isi ADMIN_PASSWORD, biarkan COOKIE_SECURE=1 dan BEHIND_PROXY=1
sudo chown -R cakung:cakung /opt/cakung-barat && sudo chmod 600 .env

# 3. Jalankan sebagai layanan
sudo cp deploy/cakung-barat.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now cakung-barat

# 4. Nginx + HTTPS (ganti contoh.go.id di deploy/nginx.conf dengan domain Anda)
sudo cp deploy/nginx.conf /etc/nginx/sites-available/cakung-barat
sudo ln -s /etc/nginx/sites-available/cakung-barat /etc/nginx/sites-enabled/
sudo certbot --nginx -d domain-anda
sudo nginx -t && sudo systemctl reload nginx
```

Alternatif: `docker compose up -d --build` (isi `.env` dulu), lalu arahkan Nginx/Caddy ke port 8000.

> **HTTPS wajib** untuk penggunaan resmi. Tanpa HTTPS, password admin terkirim tanpa enkripsi. Bila belum memakai HTTPS,
> ubah `COOKIE_SECURE=0` (hanya untuk uji coba, jangan untuk publik).

## Cadangan data

Seluruh data ada di folder `data/`. Jadwalkan `backup.sh` lewat cron (contoh di dalam skrip), dan simpan salinannya
di tempat lain (bukan hanya di server yang sama).

## Hal yang perlu Anda lakukan sebelum diumumkan ke publik

1. **Isi kontak resmi:** Dashboard → Pengaturan Portal (telepon, WhatsApp, email, jam pelayanan, alamat). Pastikan datanya sudah diverifikasi pihak kelurahan.
2. **Periksa isi Panduan Layanan** (Dashboard → Layanan Kelurahan). Teks awalnya hanya panduan umum; sesuaikan dengan persyaratan resmi.
3. **Isi berita/pengumuman pertama** agar beranda tidak kosong.
4. **Musik latar:** secara bawaan diputar dari server pihak ketiga (`ncltr.info`). Untuk andal, unggah file musik sendiri di Pengaturan Portal (maksimal 15 MB). Pastikan Anda memiliki hak pakainya.
5. **Kebijakan data pribadi:** formulir pengaduan menyimpan nama dan kontak warga. Sebaiknya kelurahan menetapkan siapa yang boleh mengakses akun admin dan berapa lama data disimpan.

## Batasan yang perlu diketahui

- Dirancang untuk satu server dengan trafik wajar (portal kelurahan). SQLite sangat cukup untuk ini; bila nanti perlu banyak server, database perlu diganti (misalnya PostgreSQL).
- Hanya ada satu akun admin. Belum ada banyak peran/pengguna, notifikasi email, atau pelacakan tiket oleh warga.
- Pembatasan percobaan login dan pengiriman pengaduan memakai alamat IP; di balik proxy, pastikan `BEHIND_PROXY=1`.
- `smoke_test.py` menguji backend memakai database sementara (`python smoke_test.py`), jalankan lagi setiap kali Anda mengubah `app.py`.
