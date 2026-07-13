import os
import requests

TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
KEPSEK_CHAT_ID = os.environ.get("KEPSEK_CHAT_ID", "")  # bisa dibuat multi chat_id per sekolah nantinya


def kirim_telegram(pesan: str, chat_id: str = None) -> bool:
    """Kirim notifikasi ke Telegram. Kembalikan True kalau berhasil."""
    chat_id = chat_id or KEPSEK_CHAT_ID
    if not TELEGRAM_BOT_TOKEN or not chat_id:
        print("[notifier] TELEGRAM_BOT_TOKEN / chat_id belum diset, lewati pengiriman.")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": pesan, "parse_mode": "HTML"}, timeout=10)
        return r.ok
    except requests.RequestException as e:
        print(f"[notifier] gagal kirim telegram: {e}")
        return False


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
