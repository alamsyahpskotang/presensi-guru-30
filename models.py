from datetime import datetime
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()

TOLERANSI_TELAT_MENIT = 15


class Guru(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama = db.Column(db.String(120), nullable=False)
    nip = db.Column(db.String(40))
    mapel = db.Column(db.String(80))
    pin = db.Column(db.String(10), nullable=False, default="0000")  # PIN 4-6 digit, login sederhana


class Kelas(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nama_kelas = db.Column(db.String(40), nullable=False)
    kode_qr = db.Column(db.String(60), unique=True, nullable=False)  # isi QR yang ditempel di kelas


class Jadwal(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guru_id = db.Column(db.Integer, db.ForeignKey("guru.id"), nullable=False)
    kelas_id = db.Column(db.Integer, db.ForeignKey("kelas.id"), nullable=False)
    hari = db.Column(db.String(10), nullable=False)  # Senin, Selasa, ...
    jam_ke = db.Column(db.String(20))
    jam_mulai = db.Column(db.String(5), nullable=False)  # "07:30"
    jam_selesai = db.Column(db.String(5), nullable=False)  # "09:00"
    mapel = db.Column(db.String(80))

    guru = db.relationship("Guru")
    kelas = db.relationship("Kelas")


class SesiPresensi(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    jadwal_id = db.Column(db.Integer, db.ForeignKey("jadwal.id"), nullable=False)
    tanggal = db.Column(db.Date, nullable=False, default=datetime.utcnow)

    waktu_scan_masuk = db.Column(db.DateTime)
    waktu_scan_keluar = db.Column(db.DateTime)

    status_masuk = db.Column(db.String(20))  # tepat_waktu, telat, tidak_hadir
    menit_telat = db.Column(db.Integer, default=0)

    foto_kegiatan_path = db.Column(db.String(200))
    ttd_siswa_path = db.Column(db.String(200))
    nama_siswa_verifikasi = db.Column(db.String(120))
    waktu_ttd = db.Column(db.DateTime)

    catatan = db.Column(db.String(300))

    jadwal = db.relationship("Jadwal")

    def hitung_status_masuk(self):
        if not self.waktu_scan_masuk:
            self.status_masuk = "tidak_hadir"
            return
        jam_mulai = datetime.strptime(self.jadwal.jam_mulai, "%H:%M").time()
        target = datetime.combine(self.waktu_scan_masuk.date(), jam_mulai)
        selisih_menit = (self.waktu_scan_masuk - target).total_seconds() / 60
        if selisih_menit <= TOLERANSI_TELAT_MENIT:
            self.status_masuk = "tepat_waktu"
            self.menit_telat = 0
        else:
            self.status_masuk = "telat"
            self.menit_telat = int(selisih_menit)

    def kelengkapan_bukti(self):
        # Guru telat tetap wajib isi foto + TTD siswa di sisa jam pelajaran
        return bool(self.foto_kegiatan_path) and bool(self.ttd_siswa_path)

    def status_keluar(self, toleransi_menit=2):
        """Bandingkan waktu scan keluar dengan jam_selesai jadwal.
        Toleransi beberapa menit dianggap tepat waktu (jam dinding tidak selalu presisi detik)."""
        if not self.waktu_scan_keluar:
            return None
        jam_selesai = datetime.strptime(self.jadwal.jam_selesai, "%H:%M").time()
        target = datetime.combine(self.waktu_scan_keluar.date(), jam_selesai)
        selisih_menit = (self.waktu_scan_keluar - target).total_seconds() / 60

        if abs(selisih_menit) <= toleransi_menit:
            return "tepat_waktu"
        elif selisih_menit < 0:
            return "sebelum_waktunya"
        else:
            return "kelebihan_waktu"

    def status_keluar(self, toleransi_menit=5):
        """Bandingkan waktu scan keluar dengan jam_selesai jadwal.
        Toleransi 5 menit dianggap tepat waktu (menghindari selisih detik dianggap salah)."""
        if not self.waktu_scan_keluar:
            return None
        jam_selesai = datetime.strptime(self.jadwal.jam_selesai, "%H:%M").time()
        target = datetime.combine(self.waktu_scan_keluar.date(), jam_selesai)
        selisih_menit = (self.waktu_scan_keluar - target).total_seconds() / 60

        if abs(selisih_menit) <= toleransi_menit:
            return "tepat_waktu"
        elif selisih_menit < 0:
            return "sebelum_waktunya"
        else:
            return "kelebihan_waktu"


class PengajuanIzin(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guru_id = db.Column(db.Integer, db.ForeignKey("guru.id"), nullable=False)
    tanggal = db.Column(db.Date, nullable=False)
    kategori = db.Column(db.String(20), nullable=False)  # sakit, dinas_luar, cuti
    jam_terdampak = db.Column(db.String(100), default="semua")  # "semua" atau daftar jam_ke dipisah koma, mis "1-2,3-4"
    keterangan = db.Column(db.String(300))
    guru_pengganti_nama = db.Column(db.String(120))  # opsional

    status = db.Column(db.String(20), default="menunggu")  # menunggu, disetujui, ditolak
    diajukan_pada = db.Column(db.DateTime, default=datetime.utcnow)
    diputuskan_pada = db.Column(db.DateTime)
    catatan_kepsek = db.Column(db.String(300))

    guru = db.relationship("Guru")

    def berlaku_untuk_jam(self, jam_ke):
        if self.status != "disetujui":
            return False
        if self.jam_terdampak == "semua":
            return True
        return jam_ke in [j.strip() for j in self.jam_terdampak.split(",")]

    def label_kategori(self):
        return {"sakit": "Sakit", "dinas_luar": "Dinas luar", "cuti": "Cuti"}.get(self.kategori, self.kategori)


class NotifikasiLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    guru_id = db.Column(db.Integer, db.ForeignKey("guru.id"))
    jenis = db.Column(db.String(40))  # telat_berulang, tidak_hadir, rekap_harian
    pesan = db.Column(db.Text)
    waktu_kirim = db.Column(db.DateTime, default=datetime.utcnow)
    terkirim = db.Column(db.Boolean, default=False)
