"""Generate QR code siap cetak untuk tiap kelas.
Jalankan: python generate_qr.py
Hasil disimpan di folder qr_kelas/ (satu file PNG per kelas, sudah ada label nama kelas).
"""
import os
import qrcode
from PIL import Image, ImageDraw, ImageFont
from app import app
from models import Kelas

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qr_kelas")
os.makedirs(OUT_DIR, exist_ok=True)


def buat_qr_dengan_label(kode_qr: str, label: str, path_output: str):
    qr = qrcode.QRCode(box_size=10, border=2)
    qr.add_data(kode_qr)
    qr.make(fit=True)
    img_qr = qr.make_image(fill_color="black", back_color="white").convert("RGB")

    lebar = img_qr.width
    tinggi_label = 60
    kanvas = Image.new("RGB", (lebar, img_qr.height + tinggi_label), "white")
    kanvas.paste(img_qr, (0, 0))

    draw = ImageDraw.Draw(kanvas)
    try:
        font = ImageFont.truetype("DejaVuSans-Bold.ttf", 28)
    except OSError:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((lebar - text_w) / 2, img_qr.height + 10), label, fill="black", font=font)

    kanvas.save(path_output)


with app.app_context():
    kelas_list = Kelas.query.all()
    if not kelas_list:
        print("Belum ada data kelas. Jalankan seed.py dulu.")
    for k in kelas_list:
        out_path = os.path.join(OUT_DIR, f"{k.nama_kelas.replace('/', '-')}.png")
        buat_qr_dengan_label(k.kode_qr, f"Kelas {k.nama_kelas}", out_path)
        print(f"Tersimpan: {out_path}")

    print(f"\nSelesai. Print semua file di folder '{OUT_DIR}' dan tempel di tiap ruang kelas.")
