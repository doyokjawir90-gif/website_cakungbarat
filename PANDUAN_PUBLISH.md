# Panduan Publikasi Website Kelurahan Cakung Barat

## Status paket
Paket ini sudah memakai aplikasi Flask dengan database SQLite di server, login admin di sisi server,
dan penyimpanan unggahan di server. Ini bukan website demo berbasis localStorage.

## Cara mendapatkan link publik menggunakan Render
1. Buat akun di https://render.com/ dan masuk.
2. Buat repository GitHub baru, misalnya `website-cakung-barat`.
3. Ekstrak folder `produksi` dari ZIP ini. Unggah seluruh isi folder `produksi` ke repository
   (file `render.yaml` harus berada di root repository).
4. Di Render pilih **New +** lalu **Blueprint**, hubungkan repository GitHub tersebut,
   kemudian pilih repository `website-cakung-barat`.
5. Render akan membaca `render.yaml`. Pastikan layanan menggunakan paket berbayar yang mendukung
   persistent disk. Konfigurasi proyek memakai disk `/var/data` agar database dan unggahan tetap ada
   setelah aplikasi restart.
6. Saat diminta mengisi environment variable `ADMIN_PASSWORD`, buat password admin yang kuat dan unik.
   Jangan kirim password ini ke siapa pun dan jangan masukkan ke file publik.
7. Selesaikan proses deploy. Setelah berhasil, Render akan memberikan alamat HTTPS berakhiran
   `.onrender.com`. Itulah link publik website.
8. Buka link tersebut dan uji halaman publik, formulir pengaduan, serta login admin.
9. Untuk memakai domain sendiri, tambahkan domain melalui pengaturan layanan Render dan ikuti
   petunjuk DNS yang diberikan Render.

## Catatan penting
- Saya belum membuat link publik karena proses deploy membutuhkan akun hosting milik pemilik website,
  persetujuan penggunaan layanan, dan pengisian password admin. Link belum ada sebelum deploy selesai.
- Jangan gunakan hosting statis seperti GitHub Pages untuk aplikasi ini. Backend Flask diperlukan
  agar login, database, pengaduan, dan unggahan berfungsi.
- Jangan membagikan password admin. Setelah deploy, simpan kredensial secara aman.
- Sebelum diumumkan sebagai portal resmi, pastikan pihak Kelurahan memverifikasi isi berita, kontak,
  alamat, persyaratan layanan, dan kebijakan pengelolaan data warga.
- Paket Render pada `render.yaml` menggunakan persistent disk berbayar; paket gratis tanpa disk
  permanen tidak cocok untuk menyimpan database dan file unggahan aplikasi ini.

## Alternatif
Jika sudah memiliki VPS Ubuntu dan domain, gunakan instruksi instalasi di `README.md` dan skrip
`install.sh` yang tersedia di paket.
