"""Hapus kelas contoh lama (VII-A, VII-B, VIII-A, VIII-B, IX-A) beserta jadwal
dan data presensi terkait. Jalankan sekali: python hapus_kelas_contoh.py
Pastikan $env:DATABASE_URL sudah diset ke database Render sebelum jalankan ini.
"""
from app import app
from models import db, Kelas, Jadwal, SesiPresensi

KELAS_CONTOH_LAMA = ["VII-A", "VII-B", "VIII-A", "VIII-B", "IX-A"]

with app.app_context():
    for nama_kelas in KELAS_CONTOH_LAMA:
        kelas = Kelas.query.filter_by(nama_kelas=nama_kelas).first()
        if not kelas:
            print(f"Kelas '{nama_kelas}' tidak ditemukan, dilewati.")
            continue

        jadwal_terkait = Jadwal.query.filter_by(kelas_id=kelas.id).all()
        total_sesi_dihapus = 0
        for j in jadwal_terkait:
            sesi_terkait = SesiPresensi.query.filter_by(jadwal_id=j.id).all()
            for s in sesi_terkait:
                db.session.delete(s)
            total_sesi_dihapus += len(sesi_terkait)
            db.session.delete(j)

        print(f"Menghapus kelas '{nama_kelas}' beserta {len(jadwal_terkait)} baris jadwal "
              f"dan {total_sesi_dihapus} baris data presensi terkait.")
        db.session.delete(kelas)

    db.session.commit()
    print("\nSelesai. Sisa jumlah kelas sekarang:", Kelas.query.count())
