"""Isi data contoh (guru, kelas, jadwal) untuk pilot SMPN 30. Jalankan sekali: python seed.py"""
from datetime import datetime
from app import app
from models import db, Guru, Kelas, Jadwal

with app.app_context():
    db.create_all()

    if Guru.query.count() == 0:
        g1 = Guru(nama="Bu Sari", nip="19800101", mapel="IPA", pin="1234")
        g2 = Guru(nama="Pak Budi", nip="19790202", mapel="Matematika", pin="5678")
        db.session.add_all([g1, g2])
        db.session.commit()

        k1 = Kelas(nama_kelas="VII-A", kode_qr="KELAS-VIIA-SMPN30")
        k2 = Kelas(nama_kelas="VIII-B", kode_qr="KELAS-VIIIB-SMPN30")
        db.session.add_all([k1, k2])
        db.session.commit()

        hari_ini = datetime.now().strftime("%A")
        hari_id_map = {
            "Monday": "Senin", "Tuesday": "Selasa", "Wednesday": "Rabu",
            "Thursday": "Kamis", "Friday": "Jumat", "Saturday": "Sabtu", "Sunday": "Minggu"
        }
        hari = hari_id_map[hari_ini]

        j1 = Jadwal(guru_id=g1.id, kelas_id=k1.id, hari=hari, jam_ke="3-4",
                    jam_mulai="07:45", jam_selesai="09:15", mapel="IPA")
        j2 = Jadwal(guru_id=g2.id, kelas_id=k2.id, hari=hari, jam_ke="1-2",
                    jam_mulai="07:00", jam_selesai="08:30", mapel="Matematika")
        db.session.add_all([j1, j2])
        db.session.commit()

        print(f"Data contoh dibuat untuk hari {hari}.")
        print(f"QR kelas VII-A: {k1.kode_qr}")
        print(f"QR kelas VIII-B: {k2.kode_qr}")
        print(f"Login Bu Sari -> PIN: 1234")
        print(f"Login Pak Budi -> PIN: 5678")
    else:
        print("Data sudah ada, tidak diisi ulang.")
