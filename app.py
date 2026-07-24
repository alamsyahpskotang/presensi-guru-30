import os
import base64
import calendar
import qrcode
import requests
from io import BytesIO
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_file, Response
from sqlalchemy import inspect, text
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

try:
    import cloudinary
    import cloudinary.uploader
    _CLOUDINARY_TERSEDIA = True
except ImportError:
    _CLOUDINARY_TERSEDIA = False

from models import db, Guru, Kelas, Jadwal, SesiPresensi, PengajuanIzin, NotifikasiLog, PenerimaNotifikasi, hitung_jarak_meter
from notifier import kirim_telegram, pesan_tidak_hadir, pesan_telat_berulang, pesan_izin_diajukan, pesan_rekap_harian


def kirim_notifikasi_semua(pesan):
    """
    Kirim notifikasi ke SEMUA penerima: gabungan dari env var KEPSEK_CHAT_ID
    (diisi manual) DAN semua orang yang sudah daftar sendiri lewat bot
    Telegram (tabel PenerimaNotifikasi, status aktif).
    """
    daftar = []
    env_chat_id = os.environ.get("KEPSEK_CHAT_ID", "")
    if env_chat_id:
        daftar.extend([c.strip() for c in env_chat_id.split(",") if c.strip()])

    try:
        pendaftar_aktif = PenerimaNotifikasi.query.filter_by(aktif=True).all()
        daftar.extend([p.chat_id for p in pendaftar_aktif])
    except Exception as e:
        print(f"[notifikasi] gagal ambil daftar pendaftar dari database: {e}")

    daftar_unik = list(dict.fromkeys(daftar))  # buang duplikat, jaga urutan
    if not daftar_unik:
        print("[notifikasi] tidak ada penerima sama sekali, lewati pengiriman.")
        return False

    return kirim_telegram(pesan, chat_id=",".join(daftar_unik))

try:
    from zoneinfo import ZoneInfo
    _WIB = ZoneInfo("Asia/Jakarta")
except Exception:
    _WIB = None


def waktu_sekarang():
    """
    Selalu mengembalikan waktu Indonesia Barat (WIB / UTC+7), apapun zona
    waktu server-nya. PENTING: hosting seperti Render default jalan di UTC,
    jadi kalau pakai datetime.now() biasa, jam yang dipakai untuk cek
    tepat-waktu/telat akan meleset 7 jam dari waktu Indonesia.
    """
    if _WIB is not None:
        return datetime.now(_WIB).replace(tzinfo=None)
    return datetime.now() + timedelta(hours=7)  # fallback kalau zoneinfo tidak tersedia


_FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts", "DejaVuSans-Bold.ttf")


def _muat_font(ukuran):
    try:
        return ImageFont.truetype(_FONT_PATH, ukuran)
    except OSError:
        return ImageFont.load_default()


LEBAR_MAKS_FOTO = 900  # cukup untuk dokumentasi kegiatan, drastis mengurangi ukuran file


def tempel_watermark_foto(foto_bytes, waktu, lat=None, lng=None, label_lokasi=None):
    """
    Tempelkan watermark tanggal/jam dan koordinat lokasi ke pojok kiri
    bawah foto, mirip aplikasi kamera timestamp. Dilakukan di server
    (bukan di HP) supaya tidak bisa dimanipulasi oleh guru.

    Foto juga otomatis diperkecil (maks 900px lebar) dan dikompres lebih
    ketat, karena foto disimpan langsung di database - penting untuk
    menghemat kapasitas storage (terutama di tier gratis Render yang
    cuma 1 GB).
    """
    try:
        img = Image.open(BytesIO(foto_bytes)).convert("RGB")
    except Exception:
        return foto_bytes  # kalau bukan gambar valid, biarkan apa adanya

    # Perkecil kalau lebih lebar dari batas maksimal, jaga rasio aspek
    if img.width > LEBAR_MAKS_FOTO:
        rasio = LEBAR_MAKS_FOTO / img.width
        img = img.resize((LEBAR_MAKS_FOTO, int(img.height * rasio)), Image.LANCZOS)

    draw = ImageDraw.Draw(img, "RGBA")
    lebar, tinggi = img.size

    ukuran_font = max(16, int(lebar * 0.028))
    font_tebal = _muat_font(ukuran_font)
    font_kecil = _muat_font(int(ukuran_font * 0.75))

    baris = [waktu.strftime("%A, %d %B %Y - %H:%M:%S WIB")]
    if lat is not None and lng is not None:
        baris.append(f"Lokasi: {lat:.5f}, {lng:.5f}")
        if label_lokasi:
            baris.append(label_lokasi)
    else:
        baris.append("Lokasi: tidak tersedia (izin GPS ditolak)")

    HARI_ID_MAP = {"Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
                   "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"}
    BULAN_ID_MAP = {"January": "Januari", "February": "Februari", "March": "Maret", "April": "April",
                     "May": "Mei", "June": "Juni", "July": "Juli", "August": "Agustus",
                     "September": "September", "October": "Oktober", "November": "November", "December": "Desember"}
    for nama_en, nama_id in HARI_ID_MAP.items():
        baris[0] = baris[0].replace(nama_en, nama_id)
    for nama_en, nama_id in BULAN_ID_MAP.items():
        baris[0] = baris[0].replace(nama_en, nama_id)

    margin = int(lebar * 0.02)
    tinggi_baris = int(ukuran_font * 1.4)
    tinggi_pita = margin + tinggi_baris * len(baris) + margin

    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    overlay_draw = ImageDraw.Draw(overlay)
    overlay_draw.rectangle(
        [(0, tinggi - tinggi_pita), (lebar, tinggi)],
        fill=(0, 0, 0, 140),
    )
    img = Image.alpha_composite(img.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(img)

    y = tinggi - tinggi_pita + margin
    for i, teks in enumerate(baris):
        font_pakai = font_tebal if i == 0 else font_kecil
        draw.text((margin, y), teks, font=font_pakai, fill="white")
        y += tinggi_baris

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=72, optimize=True)
    return buf.getvalue()

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
DATABASE_URL = os.environ.get("DATABASE_URL")
if DATABASE_URL:
    # Render kasih URL dengan skema "postgres://", tapi SQLAlchemy versi baru
    # butuh "postgresql://" - perbaiki otomatis di sini.
    if DATABASE_URL.startswith("postgres://"):
        DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)
    app.config["SQLALCHEMY_DATABASE_URI"] = DATABASE_URL
else:
    # Fallback untuk uji coba lokal di laptop (tidak permanen, tapi cukup untuk testing)
    app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(INSTANCE_DIR, 'presensi.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "ganti-secret-key-ini-di-produksi")
KEPSEK_PIN = os.environ.get("KEPSEK_PIN", "999999")

# Koordinat sekolah untuk deteksi jarak scan (opsional - kalau tidak diset,
# lokasi tetap dicatat tapi tidak dihitung jarak/status area amannya).
SEKOLAH_LAT = os.environ.get("SEKOLAH_LAT")
SEKOLAH_LNG = os.environ.get("SEKOLAH_LNG")
SEKOLAH_LAT = float(SEKOLAH_LAT) if SEKOLAH_LAT else None
SEKOLAH_LNG = float(SEKOLAH_LNG) if SEKOLAH_LNG else None
RADIUS_AMAN_METER = int(os.environ.get("RADIUS_AMAN_METER", "200"))

# --- Cloudinary (opsional) - simpan foto/TTD sebagai file di Cloudinary,
# bukan langsung di database, supaya kapasitas database (terutama tier
# gratis Render yang cuma 1 GB) tidak cepat penuh. Kalau env var ini
# tidak diisi, sistem otomatis kembali ke cara lama (simpan di database).
CLOUDINARY_CLOUD_NAME = os.environ.get("CLOUDINARY_CLOUD_NAME")
CLOUDINARY_API_KEY = os.environ.get("CLOUDINARY_API_KEY")
CLOUDINARY_API_SECRET = os.environ.get("CLOUDINARY_API_SECRET")
CLOUDINARY_AKTIF = bool(
    _CLOUDINARY_TERSEDIA and CLOUDINARY_CLOUD_NAME and CLOUDINARY_API_KEY and CLOUDINARY_API_SECRET
)
if CLOUDINARY_AKTIF:
    cloudinary.config(
        cloud_name=CLOUDINARY_CLOUD_NAME,
        api_key=CLOUDINARY_API_KEY,
        api_secret=CLOUDINARY_API_SECRET,
        secure=True,
    )


def unggah_ke_cloudinary(file_bytes, public_id, resource_type="image"):
    """Upload ke Cloudinary, kembalikan secure_url. Return None kalau gagal
    (caller harus fallback ke simpan di database)."""
    if not CLOUDINARY_AKTIF:
        return None
    try:
        hasil = cloudinary.uploader.upload(
            BytesIO(file_bytes), public_id=public_id, resource_type=resource_type,
            folder="presensi_guru", overwrite=True,
        )
        return hasil.get("secure_url")
    except Exception as e:
        import traceback
        print(f"[cloudinary] gagal upload: {e}")
        traceback.print_exc()
        return None

db.init_app(app)

HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
KATEGORI_IZIN = {"sakit": "Sakit", "dinas_luar": "Dinas luar", "cuti": "Cuti"}


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("guru_id"):
            if request.path.startswith("/api/"):
                return jsonify({"error": "Belum login"}), 401
            return redirect(url_for("halaman_login"))
        return f(*args, **kwargs)
    return wrapper


def kepsek_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("kepsek_login"):
            return redirect(url_for("halaman_login_kepsek"))
        return f(*args, **kwargs)
    return wrapper


# ---------- Halaman ----------

@app.route("/login", methods=["GET", "POST"])
def halaman_login():
    if request.method == "POST":
        nama = request.form.get("nama")
        pin = request.form.get("pin")
        guru = Guru.query.filter_by(nama=nama, pin=pin).first()
        if not guru:
            return render_template("login.html", error="Nama atau PIN salah")
        session["guru_id"] = guru.id
        session["guru_nama"] = guru.nama
        return redirect(url_for("halaman_scan"))
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("halaman_login"))


@app.route("/migrasi-db/<kode_rahasia>")
def migrasi_db(kode_rahasia):
    """
    Endpoint darurat untuk menambahkan kolom baru ke tabel yang sudah ada
    (dipakai saat struktur model berubah setelah data sudah terlanjur ada),
    tanpa perlu akses Shell dan TANPA menghapus data lama.
    Akses lewat browser: https://domain-anda.com/migrasi-db/KODE_RAHASIA
    """
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    is_postgres = db.engine.dialect.name == "postgresql"
    tipe_binary = "BYTEA" if is_postgres else "BLOB"
    tipe_teks_pendek = "VARCHAR(40)"
    tipe_angka = "FLOAT"

    kolom_baru = {
        "foto_kegiatan_data": tipe_binary,
        "foto_kegiatan_mime": tipe_teks_pendek,
        "foto_kegiatan_url": "VARCHAR(500)",
        "ttd_siswa_data": tipe_binary,
        "ttd_siswa_mime": tipe_teks_pendek,
        "ttd_siswa_url": "VARCHAR(500)",
        "lat_masuk": tipe_angka,
        "lng_masuk": tipe_angka,
        "jarak_masuk_meter": tipe_angka,
        "lat_keluar": tipe_angka,
        "lng_keluar": tipe_angka,
        "jarak_keluar_meter": tipe_angka,
    }

    db.create_all()  # buat tabel yang sama sekali belum ada (kalau ada)

    inspector = inspect(db.engine)
    tabel_ada = inspector.get_table_names()
    if "sesi_presensi" not in tabel_ada:
        return "Tabel sesi_presensi belum ada sama sekali - db.create_all() seharusnya sudah membuatnya. Coba refresh."

    kolom_ada = [c["name"] for c in inspector.get_columns("sesi_presensi")]
    ditambahkan = []

    with db.engine.connect() as conn:
        for nama, tipe in kolom_baru.items():
            if nama not in kolom_ada:
                conn.execute(text(f"ALTER TABLE sesi_presensi ADD COLUMN {nama} {tipe}"))
                ditambahkan.append(nama)
        conn.commit()

    if ditambahkan:
        return "Migrasi berhasil. Kolom baru ditambahkan: " + ", ".join(ditambahkan)
    return "Tidak ada kolom baru yang perlu ditambahkan - struktur database sudah sesuai."


@app.route("/telegram/webhook/<kode_rahasia>", methods=["POST"])
def telegram_webhook(kode_rahasia):
    """
    Menerima update dari Telegram setiap kali seseorang chat bot ini.
    Kalau isinya "/start" (atau "/start KODE"), orang itu otomatis
    terdaftar sebagai penerima notifikasi - tidak perlu Chat ID diinput
    manual oleh admin lagi.
    """
    kode_webhook_asli = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_webhook_asli:
        return "Kode rahasia salah", 403

    update = request.get_json(silent=True) or {}
    pesan_masuk = update.get("message") or {}
    chat = pesan_masuk.get("chat") or {}
    chat_id = str(chat.get("id", "")).strip()
    teks = (pesan_masuk.get("text") or "").strip()

    if not chat_id:
        return jsonify({"ok": True})

    nama_pengirim = (chat.get("first_name") or "").strip()
    if chat.get("last_name"):
        nama_pengirim += " " + chat.get("last_name").strip()
    if not nama_pengirim:
        nama_pengirim = chat.get("username") or f"Chat {chat_id}"

    if teks.startswith("/start"):
        kode_daftar_wajib = os.environ.get("TELEGRAM_KODE_DAFTAR", "").strip()
        bagian = teks.split(maxsplit=1)
        kode_diberikan = bagian[1].strip() if len(bagian) > 1 else ""

        if kode_daftar_wajib and kode_diberikan != kode_daftar_wajib:
            kirim_telegram(
                "Maaf, pendaftaran gagal - kode tidak sesuai. "
                "Silakan gunakan link pendaftaran resmi dari admin sekolah.",
                chat_id=chat_id,
            )
            return jsonify({"ok": True})

        penerima = PenerimaNotifikasi.query.filter_by(chat_id=chat_id).first()
        if penerima:
            penerima.aktif = True
            penerima.nama = nama_pengirim
            db.session.commit()
            kirim_telegram(
                f"Halo {nama_pengirim}! Anda sudah terdaftar sebelumnya untuk menerima "
                f"notifikasi presensi guru GALUTA - SMPN 30 Tangerang.",
                chat_id=chat_id,
            )
        else:
            db.session.add(PenerimaNotifikasi(chat_id=chat_id, nama=nama_pengirim, aktif=True))
            db.session.commit()
            kirim_telegram(
                f"Halo {nama_pengirim}! Pendaftaran berhasil ✅\n\n"
                f"Anda akan menerima notifikasi otomatis presensi guru GALUTA - SMPN 30 Tangerang "
                f"(guru tidak hadir, keterlambatan berulang, pengajuan izin baru).\n\n"
                f"Kirim /stop kapan saja kalau ingin berhenti menerima notifikasi.",
                chat_id=chat_id,
            )

    elif teks.startswith("/stop"):
        penerima = PenerimaNotifikasi.query.filter_by(chat_id=chat_id).first()
        if penerima and penerima.aktif:
            penerima.aktif = False
            db.session.commit()
            kirim_telegram("Anda telah berhenti menerima notifikasi. Kirim /start kapan saja untuk aktif lagi.", chat_id=chat_id)

    return jsonify({"ok": True})


@app.route("/admin/set-telegram-webhook/<kode_rahasia>")
def set_telegram_webhook(kode_rahasia):
    """Jalankan SEKALI lewat browser untuk menghubungkan bot Telegram ke
    aplikasi ini, supaya pendaftaran otomatis lewat /start bisa berfungsi."""
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        return "TELEGRAM_BOT_TOKEN belum diset di Environment Variables", 400

    kode_webhook = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "ganti-kode-ini")
    webhook_url = request.host_url.rstrip("/") + url_for("telegram_webhook", kode_rahasia=kode_webhook)

    try:
        resp = requests.post(f"https://api.telegram.org/bot{token}/setWebhook", data={"url": webhook_url}, timeout=10)
        return f"Webhook diarahkan ke: {webhook_url}<br><br>Respons Telegram: {resp.text}"
    except requests.RequestException as e:
        return f"Gagal menghubungi Telegram: {e}", 500


@app.route("/setup-data-awal/<kode_rahasia>")
def setup_data_awal(kode_rahasia):
    """
    Endpoint darurat untuk mengisi data guru/kelas/jadwal contoh tanpa
    perlu akses Shell (fitur Shell dikunci di tier gratis Render).
    Akses lewat browser: https://domain-anda.com/setup-data-awal/KODE_RAHASIA
    KODE_RAHASIA diatur lewat env var SETUP_SECRET - WAJIB diganti dari default.
    """
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    if Guru.query.count() > 0:
        return "Data guru sudah ada (%d guru), tidak diisi ulang. Hapus manual dulu kalau mau reset." % Guru.query.count()

    g1 = Guru(nama="Bu Sari", nip="19800101", mapel="IPA", pin="1234")
    g2 = Guru(nama="Pak Budi", nip="19790202", mapel="Matematika", pin="5678")
    db.session.add_all([g1, g2])
    db.session.commit()

    k1 = Kelas(nama_kelas="VII-A", kode_qr="KELAS-VIIA-SMPN30")
    k2 = Kelas(nama_kelas="VIII-B", kode_qr="KELAS-VIIIB-SMPN30")
    db.session.add_all([k1, k2])
    db.session.commit()

    hari_ini = HARI_ID[waktu_sekarang().weekday()]
    j1 = Jadwal(guru_id=g1.id, kelas_id=k1.id, hari=hari_ini, jam_ke="3-4",
                jam_mulai="07:45", jam_selesai="09:15", mapel="IPA")
    j2 = Jadwal(guru_id=g2.id, kelas_id=k2.id, hari=hari_ini, jam_ke="1-2",
                jam_mulai="07:00", jam_selesai="08:30", mapel="Matematika")
    db.session.add_all([j1, j2])
    db.session.commit()

    return "Berhasil! Data contoh dibuat untuk hari %s. Login: Bu Sari/1234 atau Pak Budi/5678" % hari_ini


@app.route("/admin/import-excel/<kode_rahasia>", methods=["GET", "POST"])
def admin_import_excel(kode_rahasia):
    """
    Upload file jadwal Excel (format sama seperti jadwal_smpn30_contoh.xlsx)
    langsung dari browser, tanpa perlu akses Shell (dikunci di tier gratis Render).
    Akses: https://domain-anda.com/admin/import-excel/KODE_RAHASIA
    KODE_RAHASIA pakai env var yang sama dengan setup-data-awal (SETUP_SECRET).

    Bersifat UPSERT: guru/kelas yang namanya sudah ada akan diperbarui
    (bukan dobel), jadwal lama untuk kombinasi guru+kelas+hari+jam_ke yang
    sama akan ditimpa. TIDAK menghapus data presensi yang sudah tercatat.
    """
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    if request.method == "GET":
        return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia)

    file = request.files.get("file_excel")
    if not file or file.filename == "":
        return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia,
                                error="Belum ada file yang dipilih")

    try:
        xl = pd.read_excel(file, sheet_name=None, dtype=str)
    except Exception as e:
        return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia,
                                error=f"Gagal membaca file Excel: {e}")

    kolom_wajib = {
        "Guru": ["nama", "nip", "mapel", "pin"],
        "Kelas": ["nama_kelas", "kode_qr"],
        "Jadwal": ["guru_nama", "kelas_nama", "hari", "jam_ke", "jam_mulai", "jam_selesai", "mapel"],
    }
    for sheet, kolom_list in kolom_wajib.items():
        if sheet not in xl:
            return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia,
                                    error=f"File tidak punya sheet '{sheet}'")
        hilang = [k for k in kolom_list if k not in xl[sheet].columns]
        if hilang:
            return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia,
                                    error=f"Sheet '{sheet}' kurang kolom: {hilang}")

    df_guru = xl["Guru"].dropna(subset=["nama"])
    df_kelas = xl["Kelas"].dropna(subset=["nama_kelas"])
    df_jadwal = xl["Jadwal"].dropna(subset=["guru_nama", "kelas_nama"])

    HARI_VALID = {"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"}

    def format_jam(nilai):
        if hasattr(nilai, "strftime"):
            return nilai.strftime("%H:%M")
        s = str(nilai).strip()
        if len(s) == 4 and s.isdigit():
            s = f"{s[:2]}:{s[2:]}"
        return s

    hasil = {"guru_baru": 0, "guru_update": 0, "kelas_baru": 0, "kelas_update": 0,
             "jadwal_baru": 0, "jadwal_update": 0, "baris_dilewati": []}

    peta_guru = {}
    for _, row in df_guru.iterrows():
        nama = row["nama"].strip()
        guru = Guru.query.filter_by(nama=nama).first()
        if guru:
            guru.nip = row.get("nip") or guru.nip
            guru.mapel = row.get("mapel") or guru.mapel
            guru.pin = str(row.get("pin") or guru.pin)
            hasil["guru_update"] += 1
        else:
            guru = Guru(nama=nama, nip=row.get("nip", ""), mapel=row.get("mapel", ""),
                        pin=str(row.get("pin", "0000")))
            db.session.add(guru)
            hasil["guru_baru"] += 1
        db.session.flush()
        peta_guru[nama] = guru.id

    peta_kelas = {}
    for _, row in df_kelas.iterrows():
        nama_kelas = row["nama_kelas"].strip()
        kode_qr = row["kode_qr"].strip()
        kelas = Kelas.query.filter_by(nama_kelas=nama_kelas).first()
        if kelas:
            kelas.kode_qr = kode_qr
            hasil["kelas_update"] += 1
        else:
            kelas = Kelas(nama_kelas=nama_kelas, kode_qr=kode_qr)
            db.session.add(kelas)
            hasil["kelas_baru"] += 1
        db.session.flush()
        peta_kelas[nama_kelas] = kelas.id

    for i, row in df_jadwal.iterrows():
        guru_nama = row["guru_nama"].strip()
        kelas_nama = row["kelas_nama"].strip()
        hari = row["hari"].strip().capitalize()

        if guru_nama not in peta_guru:
            hasil["baris_dilewati"].append(f"Baris {i + 2}: guru '{guru_nama}' tidak ada di sheet Guru")
            continue
        if kelas_nama not in peta_kelas:
            hasil["baris_dilewati"].append(f"Baris {i + 2}: kelas '{kelas_nama}' tidak ada di sheet Kelas")
            continue
        if hari not in HARI_VALID:
            hasil["baris_dilewati"].append(f"Baris {i + 2}: hari '{hari}' tidak valid")
            continue

        guru_id = peta_guru[guru_nama]
        kelas_id = peta_kelas[kelas_nama]
        jam_ke = str(row["jam_ke"]).strip()
        jam_mulai = format_jam(row["jam_mulai"])
        jam_selesai = format_jam(row["jam_selesai"])
        mapel = row.get("mapel", "")

        jadwal = Jadwal.query.filter_by(guru_id=guru_id, kelas_id=kelas_id, hari=hari, jam_ke=jam_ke).first()
        if jadwal:
            jadwal.jam_mulai = jam_mulai
            jadwal.jam_selesai = jam_selesai
            jadwal.mapel = mapel
            hasil["jadwal_update"] += 1
        else:
            jadwal = Jadwal(guru_id=guru_id, kelas_id=kelas_id, hari=hari, jam_ke=jam_ke,
                            jam_mulai=jam_mulai, jam_selesai=jam_selesai, mapel=mapel)
            db.session.add(jadwal)
            hasil["jadwal_baru"] += 1

    db.session.commit()
    return render_template("admin_import_excel.html", kode_rahasia=kode_rahasia, hasil=hasil)


@app.route("/qr-image/<kode_qr>.png")
def qr_image(kode_qr):
    """Generate gambar QR code untuk sebuah kode_qr kelas, langsung dari server (tidak perlu Shell)."""
    img = qrcode.make(kode_qr, box_size=10, border=2)
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return send_file(buf, mimetype="image/png")


@app.route("/kepsek/qr-kelas")
@kepsek_required
def halaman_qr_kelas():
    """Tampilkan semua QR kelas sekaligus - siap di-print atau screenshot per kelas."""
    kelas_list = Kelas.query.order_by(Kelas.nama_kelas).all()
    return render_template("kepsek_qr_kelas.html", kelas_list=kelas_list)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/scan")
@login_required
def halaman_scan():
    return render_template("scan.html", guru_nama=session.get("guru_nama"))


@app.route("/sesi/<int:sesi_id>")
@login_required
def halaman_sesi(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if sesi.jadwal.guru_id != session.get("guru_id"):
        return "Sesi ini bukan milik Anda", 403
    return render_template("sesi.html", sesi=sesi)


@app.route("/izin", methods=["GET", "POST"])
@login_required
def halaman_izin():
    if request.method == "POST":
        tanggal = datetime.strptime(request.form["tanggal"], "%Y-%m-%d").date()
        izin = PengajuanIzin(
            guru_id=session["guru_id"],
            tanggal=tanggal,
            kategori=request.form["kategori"],
            jam_terdampak=request.form.get("jam_terdampak") or "semua",
            keterangan=request.form.get("keterangan", "").strip(),
            guru_pengganti_nama=request.form.get("guru_pengganti", "").strip() or None,
            diajukan_pada=waktu_sekarang(),
        )
        db.session.add(izin)
        db.session.commit()

        pesan = pesan_izin_diajukan(session.get("guru_nama"), tanggal.strftime("%d-%m-%Y"), KATEGORI_IZIN.get(izin.kategori, izin.kategori))
        kirim_notifikasi_semua(pesan)

        return redirect(url_for("halaman_izin_riwayat"))

    return render_template("izin_form.html")


@app.route("/izin/riwayat")
@login_required
def halaman_izin_riwayat():
    daftar = PengajuanIzin.query.filter_by(guru_id=session["guru_id"]).order_by(PengajuanIzin.tanggal.desc()).all()
    return render_template("izin_riwayat.html", daftar=daftar)


@app.route("/kepsek/login", methods=["GET", "POST"])
def halaman_login_kepsek():
    if request.method == "POST":
        pin = request.form.get("pin")
        if pin == KEPSEK_PIN:
            session["kepsek_login"] = True
            return redirect(url_for("halaman_kepsek_izin"))
        return render_template("kepsek_login.html", error="PIN salah")
    return render_template("kepsek_login.html")


@app.route("/kepsek/izin")
@kepsek_required
def halaman_kepsek_izin():
    menunggu = PengajuanIzin.query.filter_by(status="menunggu").order_by(PengajuanIzin.tanggal).all()
    riwayat = PengajuanIzin.query.filter(PengajuanIzin.status != "menunggu").order_by(PengajuanIzin.diputuskan_pada.desc()).limit(20).all()
    return render_template("kepsek_izin.html", menunggu=menunggu, riwayat=riwayat)


@app.route("/kepsek/izin/<int:izin_id>/putuskan", methods=["POST"])
@kepsek_required
def kepsek_putuskan_izin(izin_id):
    izin = PengajuanIzin.query.get_or_404(izin_id)
    keputusan = request.form.get("keputusan")  # "disetujui" atau "ditolak"
    izin.status = keputusan
    izin.diputuskan_pada = waktu_sekarang()
    izin.catatan_kepsek = request.form.get("catatan", "").strip()
    db.session.commit()
    return redirect(url_for("halaman_kepsek_izin"))


@app.route("/kepsek/export")
@kepsek_required
def halaman_export():
    return render_template("kepsek_export.html")


@app.route("/kepsek/export/unduh", methods=["POST"])
@kepsek_required
def unduh_export():
    jenis = request.form.get("jenis")

    if jenis == "harian":
        tanggal = datetime.strptime(request.form["tanggal"], "%Y-%m-%d").date()
        tgl_awal = tgl_akhir = tanggal
    elif jenis == "mingguan":
        tgl_awal = datetime.strptime(request.form["minggu_dari"], "%Y-%m-%d").date()
        tgl_akhir = tgl_awal + timedelta(days=6)
    elif jenis == "bulanan":
        tahun, bulan = map(int, request.form["bulan"].split("-"))
        tgl_awal = date(tahun, bulan, 1)
        tgl_akhir = date(tahun, bulan, calendar.monthrange(tahun, bulan)[1])
    else:  # custom
        tgl_awal = datetime.strptime(request.form["tgl_awal"], "%Y-%m-%d").date()
        tgl_akhir = datetime.strptime(request.form["tgl_akhir"], "%Y-%m-%d").date()

    if tgl_akhir < tgl_awal:
        return "Tanggal akhir tidak boleh sebelum tanggal awal", 400

    sesi_list = (
        SesiPresensi.query.filter(SesiPresensi.tanggal >= tgl_awal, SesiPresensi.tanggal <= tgl_akhir)
        .order_by(SesiPresensi.tanggal, SesiPresensi.jadwal_id)
        .all()
    )

    baris_detail = []
    for s in sesi_list:
        ada_foto = bool(s.foto_kegiatan_data) or bool(s.foto_kegiatan_url)
        baris_detail.append({
            "Tanggal": s.tanggal.strftime("%Y-%m-%d"),
            "Hari": HARI_ID[s.tanggal.weekday()],
            "Guru": s.jadwal.guru.nama,
            "Kelas": s.jadwal.kelas.nama_kelas,
            "Mapel": s.jadwal.mapel,
            "Jam ke": s.jadwal.jam_ke,
            "Jadwal mulai": s.jadwal.jam_mulai,
            "Jadwal selesai": s.jadwal.jam_selesai,
            "Jam scan masuk": s.waktu_scan_masuk.strftime("%H:%M") if s.waktu_scan_masuk else "",
            "Status masuk": s.status_masuk or "",
            "Menit telat": s.menit_telat or 0,
            "Lokasi masuk": s.label_lokasi_masuk(RADIUS_AMAN_METER),
            "Foto ada": "Ya" if ada_foto else "Tidak",
            "Link foto": (request.host_url.rstrip("/") + url_for("media_foto", sesi_id=s.id)) if ada_foto else "",
            "Nama siswa TTD": s.nama_siswa_verifikasi or "",
            "Jam scan keluar": s.waktu_scan_keluar.strftime("%H:%M") if s.waktu_scan_keluar else "",
            "Status keluar": s.status_keluar() or "",
            "Lokasi keluar": s.label_lokasi_keluar(RADIUS_AMAN_METER),
        })
    df_detail = pd.DataFrame(baris_detail)

    baris_ringkasan = []
    if baris_detail:
        for guru_nama, grp in df_detail.groupby("Guru"):
            total = len(grp)
            tepat = int((grp["Status masuk"] == "tepat_waktu").sum())
            telat = int((grp["Status masuk"] == "telat").sum())
            rata_telat = grp.loc[grp["Status masuk"] == "telat", "Menit telat"].mean() if telat > 0 else 0
            lengkap = int(((grp["Foto ada"] == "Ya") & (grp["Nama siswa TTD"] != "")).sum())
            baris_ringkasan.append({
                "Guru": guru_nama,
                "Total sesi tercatat": total,
                "Tepat waktu": tepat,
                "Telat": telat,
                "Rata-rata menit telat": round(rata_telat, 1) if telat > 0 else 0,
                "Sesi lengkap (foto+TTD)": lengkap,
                "Persentase lengkap": f"{lengkap / total * 100:.0f}%" if total > 0 else "0%",
            })
    df_ringkasan = pd.DataFrame(baris_ringkasan)

    izin_list = PengajuanIzin.query.filter(
        PengajuanIzin.tanggal >= tgl_awal, PengajuanIzin.tanggal <= tgl_akhir
    ).all()
    baris_izin = [{
        "Guru": i.guru.nama,
        "Tanggal": i.tanggal.strftime("%Y-%m-%d") if hasattr(i.tanggal, "strftime") else str(i.tanggal),
        "Kategori": i.label_kategori(),
        "Status": i.status,
        "Keterangan": i.keterangan or "",
        "Catatan kepsek": i.catatan_kepsek or "",
    } for i in izin_list]
    df_izin = pd.DataFrame(baris_izin)

    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        (df_detail if not df_detail.empty else pd.DataFrame([{"Info": "Tidak ada data presensi pada rentang ini"}])).to_excel(
            writer, sheet_name="Detail Presensi", index=False)
        (df_ringkasan if not df_ringkasan.empty else pd.DataFrame([{"Info": "Tidak ada data"}])).to_excel(
            writer, sheet_name="Ringkasan per Guru", index=False)
        (df_izin if not df_izin.empty else pd.DataFrame([{"Info": "Tidak ada izin pada rentang ini"}])).to_excel(
            writer, sheet_name="Izin Resmi", index=False)

        # Lebarkan kolom otomatis supaya tidak terpotong saat dibuka
        for nama_sheet in writer.sheets:
            ws = writer.sheets[nama_sheet]
            for kolom in ws.columns:
                panjang_maks = max((len(str(c.value)) for c in kolom if c.value is not None), default=10)
                ws.column_dimensions[kolom[0].column_letter].width = min(panjang_maks + 3, 40)

    output.seek(0)
    nama_file = f"presensi_{jenis}_{tgl_awal}_{tgl_akhir}.xlsx"
    return send_file(output, as_attachment=True, download_name=nama_file,
                      mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")


@app.route("/kepsek/bersihkan-dashboard", methods=["POST"])
@kepsek_required
def bersihkan_dashboard():
    """
    Menghapus SEMUA sesi presensi (dan foto/TTD terkait) untuk tanggal
    hari ini saja - dipakai untuk membersihkan tampilan dashboard.
    Data di tanggal-tanggal lain (untuk rekap Excel/pembinaan) TIDAK
    ikut terhapus. Hanya bisa dipakai oleh kepala sekolah yang login.
    """
    hari_ini = waktu_sekarang().date()
    jumlah = SesiPresensi.query.filter_by(tanggal=hari_ini).delete()
    db.session.commit()
    return redirect(url_for("dashboard", dibersihkan=jumlah))


@app.route("/admin/diagnosa-foto/<kode_rahasia>")
def diagnosa_foto(kode_rahasia):
    """Halaman diagnostik sementara untuk cek kenapa foto tidak muncul di
    dashboard, tanpa perlu akses Shell. Tampilkan 15 sesi terbaru beserta
    status lengkap kolom foto/TTD-nya."""
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    sesi_list = SesiPresensi.query.order_by(SesiPresensi.id.desc()).limit(15).all()

    baris = "<tr><th>ID</th><th>Guru</th><th>Tgl</th><th>foto_data ada?</th><th>foto_url</th><th>ttd_data ada?</th><th>ttd_url</th></tr>"
    for s in sesi_list:
        baris += (
            f"<tr><td>{s.id}</td><td>{s.jadwal.guru.nama}</td><td>{s.tanggal}</td>"
            f"<td>{'YA (' + str(len(s.foto_kegiatan_data)) + ' bytes)' if s.foto_kegiatan_data else 'tidak'}</td>"
            f"<td>{s.foto_kegiatan_url or '-'}</td>"
            f"<td>{'YA' if s.ttd_siswa_data else 'tidak'}</td>"
            f"<td>{s.ttd_siswa_url or '-'}</td></tr>"
        )

    info_cloudinary = (
        f"CLOUDINARY_AKTIF = {CLOUDINARY_AKTIF}<br>"
        f"CLOUD_NAME terisi = {bool(CLOUDINARY_CLOUD_NAME)}<br>"
        f"API_KEY terisi = {bool(CLOUDINARY_API_KEY)}<br>"
        f"API_SECRET terisi = {bool(CLOUDINARY_API_SECRET)}<br>"
    )

    return f"<h3>Info konfigurasi Cloudinary</h3><p>{info_cloudinary}</p><h3>15 sesi terbaru</h3><table border='1' cellpadding='6'>{baris}</table>"


@app.route("/admin/diagnosa-guru/<kode_rahasia>")
def diagnosa_guru(kode_rahasia):
    """
    Halaman diagnostik untuk kasus 'guru dinyatakan tidak ada jadwal
    padahal di Excel ada' - biasanya penyebabnya guru DOBEL/DUPLIKAT di
    database (nama beda tipis antar proses import Excel yang berbeda
    waktu), sehingga akun yang dipakai login tidak nyambung dengan
    jadwal yang sebenarnya tersimpan di versi guru yang lain.

    Cara pakai: /admin/diagnosa-guru/<kode>?nama=sebagian_nama
    """
    kode_asli = os.environ.get("SETUP_SECRET", "ganti-kode-ini")
    if kode_rahasia != kode_asli:
        return "Kode rahasia salah", 403

    kata_kunci = request.args.get("nama", "").strip()

    html = "<h3>Diagnosa data guru</h3>"
    html += "<form method='get'><input name='nama' placeholder='ketik sebagian nama guru' value='" + kata_kunci + "' style='padding:8px; width:300px;'> <button type='submit'>Cari</button></form><br>"

    if not kata_kunci:
        html += "<p>Masukkan sebagian nama guru di kotak pencarian di atas, misal 'Dede'.</p>"
        return html

    guru_cocok = Guru.query.filter(Guru.nama.ilike(f"%{kata_kunci}%")).all()

    if not guru_cocok:
        html += f"<p style='color:red;'>Tidak ada guru dengan nama mengandung '{kata_kunci}'.</p>"
        return html

    if len(guru_cocok) > 1:
        html += f"<p style='color:red; font-weight:bold;'>⚠️ DITEMUKAN {len(guru_cocok)} BARIS GURU dengan nama serupa - ini kemungkinan besar PENYEBAB masalahnya (data duplikat)!</p>"
    else:
        html += f"<p style='color:green;'>Cuma ada 1 baris guru dengan nama ini - kemungkinan bukan masalah duplikat.</p>"

    html += "<table border='1' cellpadding='8'><tr><th>guru_id</th><th>Nama (persis)</th><th>PIN</th><th>Mapel</th><th>Jumlah baris di Jadwal</th></tr>"
    for g in guru_cocok:
        jumlah_jadwal = Jadwal.query.filter_by(guru_id=g.id).count()
        html += f"<tr><td>{g.id}</td><td>'{g.nama}' (panjang: {len(g.nama)} karakter)</td><td>{g.pin}</td><td>{g.mapel}</td><td>{jumlah_jadwal}</td></tr>"
    html += "</table>"

    html += "<h4>Detail jadwal per guru_id yang ditemukan</h4>"
    for g in guru_cocok:
        jadwal_list = Jadwal.query.filter_by(guru_id=g.id).order_by(Jadwal.hari, Jadwal.jam_ke).all()
        html += f"<p><b>guru_id={g.id} ('{g.nama}')</b> - {len(jadwal_list)} jadwal:</p><ul>"
        for j in jadwal_list:
            html += f"<li>{j.hari}, jam ke-{j.jam_ke} ({j.jam_mulai}-{j.jam_selesai}), kelas {j.kelas.nama_kelas}, {j.mapel}</li>"
        html += "</ul>"

    return html


@app.route("/dashboard")
def dashboard():
    hari_ini = waktu_sekarang().date()
    hari_ini_nama = HARI_ID[hari_ini.weekday()]

    sesi_list = SesiPresensi.query.filter_by(tanggal=hari_ini).all()
    jadwal_dengan_sesi = {s.jadwal_id for s in sesi_list}

    jadwal_hari_ini = Jadwal.query.filter_by(hari=hari_ini_nama).all()
    izin_hari_ini = PengajuanIzin.query.filter_by(tanggal=hari_ini, status="disetujui").all()

    izin_per_guru = {}
    for izin in izin_hari_ini:
        izin_per_guru.setdefault(izin.guru_id, []).append(izin)

    jadwal_izin = []
    for jadwal in jadwal_hari_ini:
        if jadwal.id in jadwal_dengan_sesi:
            continue
        for izin in izin_per_guru.get(jadwal.guru_id, []):
            if izin.berlaku_untuk_jam(jadwal.jam_ke):
                jadwal_izin.append((jadwal, izin))
                break

    return render_template("dashboard.html", sesi_list=sesi_list, tanggal=hari_ini, jadwal_izin=jadwal_izin)


# ---------- API ----------

@app.route("/api/guru-list")
def api_guru_list():
    guru_list = Guru.query.all()
    return jsonify([{"id": g.id, "nama": g.nama, "mapel": g.mapel} for g in guru_list])


@app.route("/api/scan-masuk", methods=["POST"])
@login_required
def api_scan_masuk():
    """Guru scan QR kelas -> cocokkan jadwal hari ini & jam sekarang -> buat/ambil sesi."""
    data = request.get_json()
    kode_qr = data.get("kode_qr")
    guru_id = session.get("guru_id")
    lat = data.get("lat")
    lng = data.get("lng")

    kelas = Kelas.query.filter_by(kode_qr=kode_qr).first()
    if not kelas:
        return jsonify({"error": "QR kelas tidak dikenali"}), 404

    sekarang = waktu_sekarang()
    hari_ini_nama = HARI_ID[sekarang.weekday()]
    jam_sekarang = sekarang.strftime("%H:%M")

    jadwal = Jadwal.query.filter_by(
        guru_id=guru_id, kelas_id=kelas.id, hari=hari_ini_nama
    ).filter(
        Jadwal.jam_selesai >= jam_sekarang
    ).order_by(Jadwal.jam_mulai).first()

    if not jadwal:
        return jsonify({"error": "Tidak ada jadwal Anda di kelas ini saat ini"}), 400

    sesi = SesiPresensi.query.filter_by(jadwal_id=jadwal.id, tanggal=sekarang.date()).first()
    if not sesi:
        sesi = SesiPresensi(jadwal_id=jadwal.id, tanggal=sekarang.date())
        db.session.add(sesi)

    sesi.jadwal = jadwal
    sesi.waktu_scan_masuk = sekarang
    sesi.hitung_status_masuk()

    if lat is not None and lng is not None:
        sesi.lat_masuk = lat
        sesi.lng_masuk = lng
        if SEKOLAH_LAT is not None and SEKOLAH_LNG is not None:
            sesi.jarak_masuk_meter = hitung_jarak_meter(lat, lng, SEKOLAH_LAT, SEKOLAH_LNG)

    db.session.commit()

    return jsonify({
        "sesi_id": sesi.id,
        "status_masuk": sesi.status_masuk,
        "menit_telat": sesi.menit_telat,
        "kelas": kelas.nama_kelas,
        "mapel": jadwal.mapel,
        "lokasi_label": sesi.label_lokasi_masuk(RADIUS_AMAN_METER),
    })


@app.route("/api/upload-foto/<int:sesi_id>", methods=["POST"])
@login_required
def api_upload_foto(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if sesi.jadwal.guru_id != session.get("guru_id"):
        return jsonify({"error": "Bukan sesi Anda"}), 403
    file = request.files.get("foto")
    if not file:
        return jsonify({"error": "Foto tidak ditemukan"}), 400

    try:
        foto_bytes = file.read()
        # Pakai koordinat yang sudah tercatat otomatis saat scan masuk (kelas yang sama),
        # supaya tidak perlu minta izin GPS lagi khusus untuk foto.
        lat = request.form.get("lat", type=float) if request.form.get("lat") else sesi.lat_masuk
        lng = request.form.get("lng", type=float) if request.form.get("lng") else sesi.lng_masuk
        label_lokasi = sesi.label_lokasi_masuk() if lat is not None else None

        foto_bytes = tempel_watermark_foto(foto_bytes, waktu_sekarang(), lat=lat, lng=lng, label_lokasi=label_lokasi)

        public_id = f"foto_kegiatan_{sesi_id}_{int(waktu_sekarang().timestamp())}"
        url_cloudinary = unggah_ke_cloudinary(foto_bytes, public_id)

        if url_cloudinary:
            sesi.foto_kegiatan_url = url_cloudinary
            sesi.foto_kegiatan_data = None  # tidak perlu simpan dobel di database
            sesi.foto_kegiatan_mime = None
        else:
            # Cloudinary belum dikonfigurasi / gagal - fallback simpan di database seperti sebelumnya
            sesi.foto_kegiatan_data = foto_bytes
            sesi.foto_kegiatan_mime = "image/jpeg"
        db.session.commit()
        return jsonify({"ok": True, "url": url_for("media_foto", sesi_id=sesi_id)})
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"[upload-foto] ERROR sesi {sesi_id}: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Gagal menyimpan foto di server: {e}"}), 500


@app.route("/api/simpan-ttd/<int:sesi_id>", methods=["POST"])
@login_required
def api_simpan_ttd(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if sesi.jadwal.guru_id != session.get("guru_id"):
        return jsonify({"error": "Bukan sesi Anda"}), 403
    data = request.get_json()
    nama_siswa = data.get("nama_siswa", "").strip()
    ttd_base64 = data.get("ttd_base64", "")

    if not nama_siswa or not ttd_base64:
        return jsonify({"error": "Nama siswa dan tanda tangan wajib diisi"}), 400

    header, encoded = ttd_base64.split(",", 1) if "," in ttd_base64 else (None, ttd_base64)

    try:
        ttd_bytes = base64.b64decode(encoded)

        public_id = f"ttd_siswa_{sesi_id}_{int(waktu_sekarang().timestamp())}"
        url_cloudinary = unggah_ke_cloudinary(ttd_bytes, public_id)

        if url_cloudinary:
            sesi.ttd_siswa_url = url_cloudinary
            sesi.ttd_siswa_data = None
            sesi.ttd_siswa_mime = None
        else:
            sesi.ttd_siswa_data = ttd_bytes
            sesi.ttd_siswa_mime = "image/png"

        sesi.nama_siswa_verifikasi = nama_siswa
        sesi.waktu_ttd = waktu_sekarang()
        db.session.commit()
        return jsonify({"ok": True})
    except Exception as e:
        db.session.rollback()
        import traceback
        print(f"[simpan-ttd] ERROR sesi {sesi_id}: {e}")
        traceback.print_exc()
        return jsonify({"error": f"Gagal menyimpan tanda tangan di server: {e}"}), 500


@app.route("/api/scan-keluar/<int:sesi_id>", methods=["POST"])
@login_required
def api_scan_keluar(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if sesi.jadwal.guru_id != session.get("guru_id"):
        return jsonify({"error": "Bukan sesi Anda"}), 403

    data = request.get_json(silent=True) or {}
    lat = data.get("lat")
    lng = data.get("lng")

    sesi.waktu_scan_keluar = waktu_sekarang()

    if lat is not None and lng is not None:
        sesi.lat_keluar = lat
        sesi.lng_keluar = lng
        if SEKOLAH_LAT is not None and SEKOLAH_LNG is not None:
            sesi.jarak_keluar_meter = hitung_jarak_meter(lat, lng, SEKOLAH_LAT, SEKOLAH_LNG)

    if not sesi.kelengkapan_bukti():
        sesi.catatan = "Data tidak lengkap: foto atau TTD siswa belum diisi"

    db.session.commit()
    return jsonify({"ok": True, "lengkap": sesi.kelengkapan_bukti(),
                     "lokasi_label": sesi.label_lokasi_keluar(RADIUS_AMAN_METER)})


def _cek_akses_media(sesi):
    """Foto & TTD siswa hanya boleh dilihat oleh guru pemilik sesi atau kepala sekolah."""
    if session.get("kepsek_login"):
        return True
    if session.get("guru_id") == sesi.jadwal.guru_id:
        return True
    return False


@app.route("/media/foto/<int:sesi_id>")
def media_foto(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if not _cek_akses_media(sesi):
        return "Tidak punya akses", 403
    if sesi.foto_kegiatan_url:
        return redirect(sesi.foto_kegiatan_url)
    if not sesi.foto_kegiatan_data:
        return "Foto belum ada", 404
    return Response(sesi.foto_kegiatan_data, mimetype=sesi.foto_kegiatan_mime or "image/jpeg")


@app.route("/media/ttd/<int:sesi_id>")
def media_ttd(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if not _cek_akses_media(sesi):
        return "Tidak punya akses", 403
    if sesi.ttd_siswa_url:
        return redirect(sesi.ttd_siswa_url)
    if not sesi.ttd_siswa_data:
        return "Tanda tangan belum ada", 404
    return Response(sesi.ttd_siswa_data, mimetype=sesi.ttd_siswa_mime or "image/png")


# ---------- Job terjadwal (dipanggil via cron eksternal / scheduler) ----------

@app.route("/api/jobs/cek-tidak-hadir", methods=["POST"])
def job_cek_tidak_hadir():
    """Jalankan setelah jam pelajaran berakhir untuk deteksi guru yang sama sekali tidak scan."""
    sekarang = waktu_sekarang()
    hari_ini_nama = HARI_ID[sekarang.weekday()]
    jam_sekarang = sekarang.strftime("%H:%M")

    jadwal_selesai = Jadwal.query.filter_by(hari=hari_ini_nama, jam_selesai=jam_sekarang).all()
    terkirim = 0
    for jadwal in jadwal_selesai:
        sesi = SesiPresensi.query.filter_by(jadwal_id=jadwal.id, tanggal=sekarang.date()).first()
        if sesi and sesi.waktu_scan_masuk:
            continue

        izin_disetujui = PengajuanIzin.query.filter_by(
            guru_id=jadwal.guru_id, tanggal=sekarang.date(), status="disetujui"
        ).all()
        if any(izin.berlaku_untuk_jam(jadwal.jam_ke) for izin in izin_disetujui):
            continue  # sudah ada izin resmi, tidak dianggap tidak hadir

        pesan = pesan_tidak_hadir(jadwal.guru.nama, jadwal.mapel, jadwal.kelas.nama_kelas, jadwal.jam_ke)
        ok = kirim_notifikasi_semua(pesan)
        db.session.add(NotifikasiLog(guru_id=jadwal.guru_id, jenis="tidak_hadir", pesan=pesan, terkirim=ok))
        terkirim += 1
    db.session.commit()
    return jsonify({"notifikasi_terkirim": terkirim})


@app.route("/api/jobs/cek-telat-berulang", methods=["POST"])
def job_cek_telat_berulang():
    """Jalankan tiap malam: cek guru yang sudah telat >=3x bulan berjalan."""
    sekarang = waktu_sekarang()
    awal_bulan = date(sekarang.year, sekarang.month, 1)
    akhir_bulan = date(sekarang.year, sekarang.month, calendar.monthrange(sekarang.year, sekarang.month)[1])
    bulan_ini_label = sekarang.strftime("%Y-%m")

    guru_list = Guru.query.all()
    terkirim = 0
    for guru in guru_list:
        jumlah_telat = (
            SesiPresensi.query.join(Jadwal)
            .filter(Jadwal.guru_id == guru.id, SesiPresensi.status_masuk == "telat")
            .filter(SesiPresensi.tanggal >= awal_bulan, SesiPresensi.tanggal <= akhir_bulan)
            .count()
        )
        if jumlah_telat == 3:  # kirim sekali saat tepat mencapai 3x
            pesan = pesan_telat_berulang(guru.nama, jumlah_telat, bulan_ini_label)
            ok = kirim_notifikasi_semua(pesan)
            db.session.add(NotifikasiLog(guru_id=guru.id, jenis="telat_berulang", pesan=pesan, terkirim=ok))
            terkirim += 1
    db.session.commit()
    return jsonify({"notifikasi_terkirim": terkirim})


def init_db():
    with app.app_context():
        db.create_all()


init_db()  # pastikan tabel ada saat dijalankan lewat gunicorn (produksi) maupun langsung


if __name__ == "__main__":
    use_https = os.environ.get("USE_HTTPS", "0") == "1"
    if use_https:
        # use_reloader dimatikan: kombinasi reloader + ssl adhoc sering macet di Windows
        app.run(host="0.0.0.0", port=5001, debug=True, use_reloader=False, ssl_context="adhoc")
    else:
        app.run(host="0.0.0.0", port=5001, debug=True)