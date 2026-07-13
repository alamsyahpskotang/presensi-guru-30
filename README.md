# Presensi Kelas - Pilot SMPN 30

Starter project Flask + PWA untuk memantau kehadiran guru penuh di kelas
(scan QR masuk/keluar, foto kegiatan, TTD verifikasi siswa, notifikasi Telegram).

## Cara menjalankan (lokal)

```bash
cd presensi_guru
pip install -r requirements.txt
python seed.py      # buat database + data contoh guru/kelas/jadwal
python app.py        # jalankan server di http://localhost:5001
```

Buka `http://localhost:5001` di HP (harus di jaringan wifi yang sama,
atau deploy ke server/VPS supaya bisa diakses dari mana saja).
Kamera dan geolocation browser butuh HTTPS kalau diakses dari domain publik
(localhost dikecualikan) — pakai layanan seperti Render/Railway yang otomatis HTTPS.

## Konfigurasi Telegram

Set environment variable sebelum menjalankan server:

```bash
export TELEGRAM_BOT_TOKEN="isi_token_bot_anda"
export KEPSEK_CHAT_ID="isi_chat_id_kepsek"
```

Job notifikasi (`/api/jobs/cek-tidak-hadir` dan `/api/jobs/cek-telat-berulang`)
dipanggil lewat cron eksternal (misal cron job di VPS, atau layanan seperti
cron-job.org) sesuai jadwal jam pelajaran sekolah.

## Login guru

Data contoh (dari `seed.py`):
- Bu Sari, PIN: 1234
- Pak Budi, PIN: 5678

Login sudah memakai session Flask - guru_id tidak lagi dikirim manual dari
client, jadi setiap sesi presensi otomatis terkunci ke guru yang login,
dan guru lain tidak bisa membuka atau mengisi sesi milik guru lain.

## Cetak QR kelas

```bash
python generate_qr.py
```

Akan membuat file PNG per kelas di folder `qr_kelas/`, sudah ada label nama
kelas di bawah QR-nya. Tinggal print dan tempel di tiap ruang kelas
(disarankan laminating supaya awet).

## Reminder otomatis TTD siswa

Halaman sesi sekarang otomatis memunculkan banner peringatan 15 menit
sebelum jam pelajaran selesai ("Serahkan HP ke siswa perwakilan..."),
dan tetap tampil (dengan pesan berbeda) kalau jam sudah lewat tapi TTD
belum diisi. Peringatan hilang otomatis begitu TTD tersimpan. Pakai HP
dengan getar aktif supaya guru terasa notifikasinya.

## Akses kamera dari HP (WAJIB dibaca sebelum uji coba)

Browser di HP (terutama Android/Chrome) **memblokir akses kamera** kalau
halaman diakses lewat alamat IP biasa (`http://192.168.x.x`) karena
dianggap tidak aman. Untuk uji coba lokal, jalankan server dalam mode
HTTPS:

```bash
# Windows PowerShell:
$env:USE_HTTPS="1"
python app.py

# Mac/Linux:
USE_HTTPS=1 python app.py
```

Catatan: mode ini otomatis mematikan auto-reload (fitur restart otomatis
saat kode diubah) karena kombinasi reloader + sertifikat HTTPS sering
membuat server macet/hang di Windows. Kalau Anda ubah kode saat mode
HTTPS aktif, matikan server (Ctrl+C) dan jalankan ulang manual.

Server akan jalan di `https://` (bukan `http://`). Saat dibuka dari HP:
- Browser akan menampilkan peringatan "Not secure" / "Your connection
  is not private" - ini **normal**, karena sertifikatnya self-signed
  (buatan sendiri, bukan dari otoritas resmi).
- Klik **Advanced** → **Proceed to [alamat] (unsafe)** untuk lanjut.
- Setelah itu kamera akan bisa diakses normal.

Mode ini hanya untuk uji coba lokal. Untuk pemakaian riil, deploy ke
hosting (lihat bagian "Deploy ke hosting" di bawah) yang otomatis
menyediakan HTTPS resmi tanpa peringatan.

## Deploy ke hosting (Render / Railway)

Project sudah siap deploy dengan `Procfile` dan `gunicorn`:

1. Push folder ini ke repository GitHub.
2. Di Render.com atau Railway.app: buat **Web Service** baru, hubungkan
   ke repo tersebut. Build command: `pip install -r requirements.txt`.
   Start command otomatis terbaca dari `Procfile`.
3. Set environment variables di dashboard hosting (jangan taruh di kode):
   - `SECRET_KEY` - string acak panjang
   - `TELEGRAM_BOT_TOKEN`
   - `KEPSEK_CHAT_ID`
   (lihat `.env.example` sebagai referensi)
4. Setelah deploy pertama, jalankan `seed.py` sekali lewat shell hosting
   (Render/Railway sediakan fitur "Shell") untuk isi data awal, lalu
   ganti dengan data jadwal riil.
5. Domain hosting otomatis HTTPS - kamera dan PWA install akan berfungsi
   normal di HP guru.
6. **Catatan database**: SQLite di free tier hosting biasanya tidak
   persisten (hilang saat redeploy). Untuk pilot singkat tidak masalah,
   tapi untuk pemakaian jangka panjang disarankan pindah ke PostgreSQL
   (Render/Railway sediakan addon gratis) - tinggal ganti
   `SQLALCHEMY_DATABASE_URI` di `app.py`.

## Cron job notifikasi Telegram

Setelah deploy, daftarkan 2 URL berikut ke layanan cron gratis seperti
cron-job.org, dijadwalkan sesuai jam pelajaran sekolah:
- `POST https://domain-anda.com/api/jobs/cek-tidak-hadir` - tiap kali
  jam pelajaran berakhir (bisa dijadwalkan tiap jam ganti pelajaran)
- `POST https://domain-anda.com/api/jobs/cek-telat-berulang` - sekali
  tiap malam (misal jam 21:00)

## Import data jadwal dari Excel

```bash
python import_excel.py nama_file.xlsx
```

File Excel harus punya 3 sheet dengan kolom persis:
- **Guru**: `nama` | `nip` | `mapel` | `pin`
- **Kelas**: `nama_kelas` | `kode_qr`
- **Jadwal**: `guru_nama` | `kelas_nama` | `hari` | `jam_ke` | `jam_mulai` | `jam_selesai` | `mapel`

Contoh file bisa dilihat di `jadwal_smpn30_contoh.xlsx` (10 guru, 5 kelas,
75 baris jadwal) - tinggal diganti isinya dengan data riil SMPN 30, jaga
format kolom tetap sama.

Import bersifat **upsert** - aman dijalankan berkali-kali, tidak akan
membuat data dobel. Guru/kelas yang namanya sudah ada akan diperbarui,
bukan diduplikasi. Baris jadwal yang mereferensikan nama guru/kelas/hari
yang tidak valid akan dilewati dengan pesan error yang jelas (nomor baris
+ alasan), tidak menghentikan proses import baris lainnya.

Setelah import data riil berhasil, jalankan `python generate_qr.py` ulang
untuk membuat QR code sesuai kelas yang baru.

## Fitur pengajuan izin (sakit / dinas luar / cuti)

Supaya guru yang berhalangan resmi tidak ikut ke-flag sebagai "tidak hadir",
sistem sekarang punya alur persetujuan izin terpisah dari presensi harian:

1. Guru login lalu buka `/izin`, ajukan izin (tanggal, kategori, jam
   terdampak, guru pengganti opsional). Kepala sekolah otomatis dapat
   notifikasi Telegram saat ada pengajuan baru.
2. Kepala sekolah login di `/kepsek/login` (PIN terpisah dari PIN guru,
   diset lewat env var `KEPSEK_PIN`), lalu approve/tolak di `/kepsek/izin`.
3. Begitu disetujui, jadwal guru itu di hari tersebut otomatis muncul di
   dashboard sebagai baris "Izin resmi" (bukan tercampur dengan status
   tidak hadir), dan job `cek-tidak-hadir` otomatis melewati jadwal
   tersebut - tidak mengirim notifikasi "tidak hadir" ke kepala sekolah.
4. Kalau izin ditolak atau tidak diajukan sama sekali, jadwal itu tetap
   diperlakukan sebagai potensi "tidak hadir" seperti biasa.

Field `PIN` kepala sekolah default `999999` - **wajib diganti** lewat env
var `KEPSEK_PIN` sebelum dipakai riil (lihat `.env.example`).

Fondasi ini disiapkan supaya nanti mudah diperluas ke notifikasi wali
murid: status harian per kelas jadi 4 kategori jelas (hadir penuh / izin
resmi dengan pengganti / izin resmi tanpa pengganti / belum ada
keterangan), dan hanya kategori yang sudah jelas statusnya yang aman
dikirim ke wali murid.

## Checklist status pengerjaan

1. ✅ **Login guru** - PIN 4 digit per guru, session-based, sesi terkunci per guru.
2. ✅ **Input data riil** - `import_excel.py` siap pakai, tinggal isi Excel
   dengan data jadwal asli SMPN 30 mengikuti format `jadwal_smpn30_contoh.xlsx`.
3. ✅ **Cetak QR kelas** - `python generate_qr.py`, hasil di folder `qr_kelas/`.
4. ✅ **Reminder TTD siswa** - otomatis muncul 15 menit sebelum jam selesai.
5. ✅ **Konfigurasi hosting** - `Procfile` + `gunicorn` siap deploy ke Render/Railway.
6. ✅ **Icon PWA** - sudah ada di `static/icons/`, terpasang di manifest.

Semua item PR sudah selesai. Sisa langkah sebelum pilot jalan riil:
kumpulkan jadwal pelajaran SMPN 30 dalam format Excel seperti contoh,
jalankan `import_excel.py`, cetak QR hasil `generate_qr.py`, lalu deploy
ke hosting sesuai panduan di atas.
