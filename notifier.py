import os
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
# Bisa diisi lebih dari satu chat ID, dipisah koma, misal:
# KEPSEK_CHAT_ID = "111111111,222222222,333333333"
# supaya kepala sekolah, wakil kepala sekolah, dan pengawas sekolah
# semuanya dapat notifikasi yang sama.
KEPSEK_CHAT_ID = os.environ.get("KEPSEK_CHAT_ID", "")


def _daftar_chat_id(chat_id_input):
    sumber = chat_id_input or KEPSEK_CHAT_ID
    return [c.strip() for c in sumber.split(",") if c.strip()]


def kirim_telegram(pesan: str, chat_id: str = None) -> bool:
    """
    Kirim notifikasi ke Telegram - bisa ke satu atau BANYAK chat ID
    sekaligus (dipisah koma di env var KEPSEK_CHAT_ID atau parameter
    chat_id). Kembalikan True kalau BERHASIL ke minimal satu penerima.
    """
    if not TELEGRAM_BOT_TOKEN:
        print("[notifier] TELEGRAM_BOT_TOKEN belum diset, lewati pengiriman.")
        return False

    daftar_penerima = _daftar_chat_id(chat_id)
    if not daftar_penerima:
        print("[notifier] Tidak ada chat_id tujuan (KEPSEK_CHAT_ID kosong), lewati pengiriman.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    ada_yang_berhasil = False
    for cid in daftar_penerima:
        try:
            r = requests.post(url, data={"chat_id": cid, "text": pesan, "parse_mode": "HTML"}, timeout=10)
            if r.ok:
                ada_yang_berhasil = True
            else:
                print(f"[notifier] gagal kirim ke chat_id {cid}: {r.status_code} {r.text}")
        except requests.RequestException as e:
            print(f"[notifier] error kirim ke chat_id {cid}: {e}")
    return ada_yang_berhasil


def pesan_tidak_hadir(nama_guru, mapel, kelas, jam_ke):
    return (
        f"⚠️ <b>Guru tidak hadir di kelas</b>\n"
        f"Guru: {nama_guru}\n"
        f"Mapel: {mapel}\n"
        f"Kelas: {kelas} (jam ke {jam_ke})\n"
        f"Tidak ada scan masuk sampai jam pelajaran berakhir."
    )


def pesan_telat_berulang(nama_guru, jumlah_telat, bulan):
    return (
        f"🔔 <b>Keterlambatan berulang</b>\n"
        f"Guru: {nama_guru}\n"
        f"Sudah telat {jumlah_telat}x di bulan {bulan}.\n"
        f"Perlu tindak lanjut pembinaan."
    )


def pesan_izin_diajukan(nama_guru, tanggal, kategori):
    return (
        f"📝 <b>Pengajuan izin baru</b>\n"
        f"Guru: {nama_guru}\n"
        f"Tanggal: {tanggal}\n"
        f"Kategori: {kategori}\n"
        f"Perlu persetujuan di dashboard."
    )


def pesan_rekap_harian(tanggal, total_sesi, lengkap, telat, tidak_hadir):
    return (
        f"📋 <b>Rekap presensi kelas - {tanggal}</b>\n"
        f"Total sesi: {total_sesi}\n"
        f"Lengkap penuh: {lengkap}\n"
        f"Telat: {telat}\n"
        f"Tidak hadir di kelas: {tidak_hadir}"
    )
