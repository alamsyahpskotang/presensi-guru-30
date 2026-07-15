"""
Generate QR code untuk tiap kelas, langsung dari file Excel jadwal
(sheet "Kelas"), tanpa perlu Flask/database/server sama sekali.
Cocok dijalankan di laptop lewat VS Code atau terminal biasa.

Cara pakai:
    pip install qrcode[pil] openpyxl pandas
    python generate_qr_dari_excel.py jadwal_smpn30_contoh.xlsx

Hasil disimpan di folder qr_kelas/ (satu file PNG per kelas, sudah ada
label nama kelas di bawah QR-nya) - siap di-print dan ditempel di kelas.
"""
import sys
import os
import pandas as pd
import qrcode
from PIL import Image, ImageDraw, ImageFont

OUT_DIR = "qr_kelas"


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
        try:
            font = ImageFont.truetype("arial.ttf", 28)  # fallback untuk Windows
        except OSError:
            font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), label, font=font)
    text_w = bbox[2] - bbox[0]
    draw.text(((lebar - text_w) / 2, img_qr.height + 10), label, fill="black", font=font)

    kanvas.save(path_output)


def main():
    if len(sys.argv) != 2:
        print("Cara pakai: python generate_qr_dari_excel.py <nama_file.xlsx>")
        sys.exit(1)

    path_excel = sys.argv[1]
    if not os.path.exists(path_excel):
        print(f"File tidak ditemukan: {path_excel}")
        sys.exit(1)

    df_kelas = pd.read_excel(path_excel, sheet_name="Kelas", dtype=str).dropna(subset=["nama_kelas"])

    os.makedirs(OUT_DIR, exist_ok=True)

    print(f"Ditemukan {len(df_kelas)} kelas. Membuat QR code...")
    for _, row in df_kelas.iterrows():
        nama_kelas = row["nama_kelas"].strip()
        kode_qr = row["kode_qr"].strip()
        nama_file = nama_kelas.replace("/", "-").replace(".", "-") + ".png"
        path_output = os.path.join(OUT_DIR, nama_file)
        buat_qr_dengan_label(kode_qr, f"Kelas {nama_kelas}", path_output)
        print(f"  Tersimpan: {path_output}")

    print(f"\nSelesai. Buka folder '{OUT_DIR}', print semua file, laminating, lalu tempel di tiap ruang kelas.")


if __name__ == "__main__":
    main()
