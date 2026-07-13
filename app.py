import os
import base64
import uuid
import calendar
from io import BytesIO
from datetime import datetime, date, timedelta
from functools import wraps
from flask import Flask, request, jsonify, render_template, send_from_directory, session, redirect, url_for, send_file
import pandas as pd

from models import db, Guru, Kelas, Jadwal, SesiPresensi, PengajuanIzin, NotifikasiLog
from notifier import kirim_telegram, pesan_tidak_hadir, pesan_telat_berulang, pesan_izin_diajukan, pesan_rekap_harian

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOTO = os.path.join(BASE_DIR, "uploads", "foto")
UPLOAD_TTD = os.path.join(BASE_DIR, "uploads", "ttd")
INSTANCE_DIR = os.path.join(BASE_DIR, "instance")

os.makedirs(UPLOAD_FOTO, exist_ok=True)
os.makedirs(UPLOAD_TTD, exist_ok=True)
os.makedirs(INSTANCE_DIR, exist_ok=True)

app = Flask(__name__)
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{os.path.join(INSTANCE_DIR, 'presensi.db')}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
app.secret_key = os.environ.get("SECRET_KEY", "ganti-secret-key-ini-di-produksi")
KEPSEK_PIN = os.environ.get("KEPSEK_PIN", "999999")
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

    hari_ini = HARI_ID[datetime.now().weekday()]
    j1 = Jadwal(guru_id=g1.id, kelas_id=k1.id, hari=hari_ini, jam_ke="3-4",
                jam_mulai="07:45", jam_selesai="09:15", mapel="IPA")
    j2 = Jadwal(guru_id=g2.id, kelas_id=k2.id, hari=hari_ini, jam_ke="1-2",
                jam_mulai="07:00", jam_selesai="08:30", mapel="Matematika")
    db.session.add_all([j1, j2])
    db.session.commit()

    return "Berhasil! Data contoh dibuat untuk hari %s. Login: Bu Sari/1234 atau Pak Budi/5678" % hari_ini


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
        )
        db.session.add(izin)
        db.session.commit()

        pesan = pesan_izin_diajukan(session.get("guru_nama"), tanggal.strftime("%d-%m-%Y"), KATEGORI_IZIN.get(izin.kategori, izin.kategori))
        kirim_telegram(pesan)

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
    izin.diputuskan_pada = datetime.now()
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
            "Foto ada": "Ya" if s.foto_kegiatan_path else "Tidak",
            "Nama siswa TTD": s.nama_siswa_verifikasi or "",
            "Jam scan keluar": s.waktu_scan_keluar.strftime("%H:%M") if s.waktu_scan_keluar else "",
            "Status keluar": s.status_keluar() or "",
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


@app.route("/dashboard")
def dashboard():
    hari_ini = date.today()
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

    kelas = Kelas.query.filter_by(kode_qr=kode_qr).first()
    if not kelas:
        return jsonify({"error": "QR kelas tidak dikenali"}), 404

    sekarang = datetime.now()
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
    db.session.commit()

    return jsonify({
        "sesi_id": sesi.id,
        "status_masuk": sesi.status_masuk,
        "menit_telat": sesi.menit_telat,
        "kelas": kelas.nama_kelas,
        "mapel": jadwal.mapel,
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

    nama_file = f"{sesi_id}_{uuid.uuid4().hex[:8]}.jpg"
    path = os.path.join(UPLOAD_FOTO, nama_file)
    file.save(path)

    sesi.foto_kegiatan_path = f"foto/{nama_file}"
    db.session.commit()
    return jsonify({"ok": True, "path": sesi.foto_kegiatan_path})


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
    nama_file = f"{sesi_id}_{uuid.uuid4().hex[:8]}.png"
    path = os.path.join(UPLOAD_TTD, nama_file)
    with open(path, "wb") as f:
        f.write(base64.b64decode(encoded))

    sesi.ttd_siswa_path = f"ttd/{nama_file}"
    sesi.nama_siswa_verifikasi = nama_siswa
    sesi.waktu_ttd = datetime.now()
    db.session.commit()
    return jsonify({"ok": True})


@app.route("/api/scan-keluar/<int:sesi_id>", methods=["POST"])
@login_required
def api_scan_keluar(sesi_id):
    sesi = SesiPresensi.query.get_or_404(sesi_id)
    if sesi.jadwal.guru_id != session.get("guru_id"):
        return jsonify({"error": "Bukan sesi Anda"}), 403
    sesi.waktu_scan_keluar = datetime.now()

    if not sesi.kelengkapan_bukti():
        sesi.catatan = "Data tidak lengkap: foto atau TTD siswa belum diisi"

    db.session.commit()
    return jsonify({"ok": True, "lengkap": sesi.kelengkapan_bukti()})


@app.route("/uploads/<path:filename>")
def serve_upload(filename):
    return send_from_directory(os.path.join(BASE_DIR, "uploads"), filename)


# ---------- Job terjadwal (dipanggil via cron eksternal / scheduler) ----------

@app.route("/api/jobs/cek-tidak-hadir", methods=["POST"])
def job_cek_tidak_hadir():
    """Jalankan setelah jam pelajaran berakhir untuk deteksi guru yang sama sekali tidak scan."""
    sekarang = datetime.now()
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
        ok = kirim_telegram(pesan)
        db.session.add(NotifikasiLog(guru_id=jadwal.guru_id, jenis="tidak_hadir", pesan=pesan, terkirim=ok))
        terkirim += 1
    db.session.commit()
    return jsonify({"notifikasi_terkirim": terkirim})


@app.route("/api/jobs/cek-telat-berulang", methods=["POST"])
def job_cek_telat_berulang():
    """Jalankan tiap malam: cek guru yang sudah telat >=3x bulan berjalan."""
    bulan_ini = datetime.now().strftime("%Y-%m")
    guru_list = Guru.query.all()
    terkirim = 0
    for guru in guru_list:
        jumlah_telat = (
            SesiPresensi.query.join(Jadwal)
            .filter(Jadwal.guru_id == guru.id, SesiPresensi.status_masuk == "telat")
            .filter(db.func.strftime("%Y-%m", SesiPresensi.tanggal) == bulan_ini)
            .count()
        )
        if jumlah_telat == 3:  # kirim sekali saat tepat mencapai 3x
            pesan = pesan_telat_berulang(guru.nama, jumlah_telat, bulan_ini)
            ok = kirim_telegram(pesan)
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
