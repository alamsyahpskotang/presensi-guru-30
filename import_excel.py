"""Import data guru, kelas, dan jadwal dari file Excel ke database.

Format Excel harus punya 3 sheet dengan kolom persis seperti ini:

Sheet "Guru":   nama | nip | mapel | pin
Sheet "Kelas":  nama_kelas | kode_qr
Sheet "Jadwal": guru_nama | kelas_nama | hari | jam_ke | jam_mulai | jam_selesai | mapel

guru_nama dan kelas_nama di sheet Jadwal harus PERSIS SAMA dengan nama di
sheet Guru/Kelas (termasuk spasi dan tanda baca), karena dipakai untuk
mencocokkan baris.

Cara pakai:
    python import_excel.py jadwal_smpn30_contoh.xlsx

Import bersifat "upsert": data guru/kelas yang namanya sudah ada akan
diperbarui (bukan dobel), dan jadwal lama untuk kombinasi guru+kelas+hari+jam_ke
yang sama akan ditimpa. Import TIDAK menghapus data presensi yang sudah ada.
"""
import sys
import pandas as pd
from app import app
from models import db, Guru, Kelas, Jadwal

KOLOM_WAJIB = {
    "Guru": ["nama", "nip", "mapel", "pin"],
    "Kelas": ["nama_kelas", "kode_qr"],
    "Jadwal": ["guru_nama", "kelas_nama", "hari", "jam_ke", "jam_mulai", "jam_selesai", "mapel"],
}

HARI_VALID = {"Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"}


def validasi_kolom(df, nama_sheet):
    kolom_hilang = [k for k in KOLOM_WAJIB[nama_sheet] if k not in df.columns]
    if kolom_hilang:
        raise ValueError(f"Sheet '{nama_sheet}' kurang kolom: {kolom_hilang}")


def format_jam(nilai):
    """Excel kadang baca jam sebagai datetime.time, kadang sebagai string. Normalisasi ke 'HH:MM'."""
    if hasattr(nilai, "strftime"):
        return nilai.strftime("%H:%M")
    s = str(nilai).strip()
    if len(s) == 4 and s.isdigit():  # "0700" -> "07:00"
        s = f"{s[:2]}:{s[2:]}"
    return s


def import_excel(path_file):
    xl = pd.read_excel(path_file, sheet_name=None, dtype=str)

    for sheet in ["Guru", "Kelas", "Jadwal"]:
        if sheet not in xl:
            raise ValueError(f"File Excel tidak punya sheet '{sheet}'")
        validasi_kolom(xl[sheet], sheet)

    df_guru = xl["Guru"].dropna(subset=["nama"])
    df_kelas = xl["Kelas"].dropna(subset=["nama_kelas"])
    df_jadwal = xl["Jadwal"].dropna(subset=["guru_nama", "kelas_nama"])

    with app.app_context():
        laporan = {"guru_baru": 0, "guru_update": 0, "kelas_baru": 0, "kelas_update": 0,
                   "jadwal_baru": 0, "jadwal_update": 0, "baris_dilewati": []}

        # --- Guru ---
        peta_guru = {}
        for _, row in df_guru.iterrows():
            nama = row["nama"].strip()
            guru = Guru.query.filter_by(nama=nama).first()
            if guru:
                guru.nip = row.get("nip", guru.nip)
                guru.mapel = row.get("mapel", guru.mapel)
                guru.pin = str(row.get("pin", guru.pin))
                laporan["guru_update"] += 1
            else:
                guru = Guru(nama=nama, nip=row.get("nip"), mapel=row.get("mapel"),
                            pin=str(row.get("pin", "0000")))
                db.session.add(guru)
                laporan["guru_baru"] += 1
            db.session.flush()
            peta_guru[nama] = guru.id

        # --- Kelas ---
        peta_kelas = {}
        for _, row in df_kelas.iterrows():
            nama_kelas = row["nama_kelas"].strip()
            kode_qr = row["kode_qr"].strip()
            kelas = Kelas.query.filter_by(nama_kelas=nama_kelas).first()
            if kelas:
                kelas.kode_qr = kode_qr
                laporan["kelas_update"] += 1
            else:
                kelas = Kelas(nama_kelas=nama_kelas, kode_qr=kode_qr)
                db.session.add(kelas)
                laporan["kelas_baru"] += 1
            db.session.flush()
            peta_kelas[nama_kelas] = kelas.id

        # --- Jadwal ---
        for i, row in df_jadwal.iterrows():
            guru_nama = row["guru_nama"].strip()
            kelas_nama = row["kelas_nama"].strip()
            hari = row["hari"].strip().capitalize()

            if guru_nama not in peta_guru:
                laporan["baris_dilewati"].append(f"Jadwal baris {i+2}: guru '{guru_nama}' tidak ada di sheet Guru")
                continue
            if kelas_nama not in peta_kelas:
                laporan["baris_dilewati"].append(f"Jadwal baris {i+2}: kelas '{kelas_nama}' tidak ada di sheet Kelas")
                continue
            if hari not in HARI_VALID:
                laporan["baris_dilewati"].append(f"Jadwal baris {i+2}: hari '{hari}' tidak valid")
                continue

            guru_id = peta_guru[guru_nama]
            kelas_id = peta_kelas[kelas_nama]
            jam_ke = str(row["jam_ke"]).strip()
            jam_mulai = format_jam(row["jam_mulai"])
            jam_selesai = format_jam(row["jam_selesai"])
            mapel = row.get("mapel", "")

            jadwal = Jadwal.query.filter_by(
                guru_id=guru_id, kelas_id=kelas_id, hari=hari, jam_ke=jam_ke
            ).first()
            if jadwal:
                jadwal.jam_mulai = jam_mulai
                jadwal.jam_selesai = jam_selesai
                jadwal.mapel = mapel
                laporan["jadwal_update"] += 1
            else:
                jadwal = Jadwal(guru_id=guru_id, kelas_id=kelas_id, hari=hari, jam_ke=jam_ke,
                                jam_mulai=jam_mulai, jam_selesai=jam_selesai, mapel=mapel)
                db.session.add(jadwal)
                laporan["jadwal_baru"] += 1

        db.session.commit()
        return laporan


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Cara pakai: python import_excel.py <nama_file.xlsx>")
        sys.exit(1)

    hasil = import_excel(sys.argv[1])

    print("=== Hasil import ===")
    print(f"Guru baru: {hasil['guru_baru']}, diperbarui: {hasil['guru_update']}")
    print(f"Kelas baru: {hasil['kelas_baru']}, diperbarui: {hasil['kelas_update']}")
    print(f"Jadwal baru: {hasil['jadwal_baru']}, diperbarui: {hasil['jadwal_update']}")

    if hasil["baris_dilewati"]:
        print(f"\n{len(hasil['baris_dilewati'])} baris dilewati karena tidak valid:")
        for pesan in hasil["baris_dilewati"]:
            print(f"  - {pesan}")
    else:
        print("\nSemua baris berhasil diimpor tanpa error.")
