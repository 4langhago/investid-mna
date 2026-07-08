"""PWA 아이콘 생성 (192x192, 512x512, maskable 포함)"""
from PIL import Image, ImageDraw, ImageFont
import os

OUT_DIR = os.path.join(os.path.dirname(__file__), "..", "icons")
os.makedirs(OUT_DIR, exist_ok=True)

BG = (0, 60, 130)      # InvestID 브랜드 남색
FG = (255, 255, 255)


def make_icon(size, padding_ratio=0.0, filename=None):
    img = Image.new("RGB", (size, size), BG)
    draw = ImageDraw.Draw(img)

    text = "ID"
    font_size = int(size * (0.42 if padding_ratio == 0 else 0.34))
    try:
        font = ImageFont.truetype("arialbd.ttf", font_size)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text(((size - tw) / 2 - bbox[0], (size - th) / 2 - bbox[1]), text, font=font, fill=FG)

    path = os.path.join(OUT_DIR, filename)
    img.save(path, "PNG")
    print("saved", path)


make_icon(192, filename="icon-192.png")
make_icon(512, filename="icon-512.png")
make_icon(512, padding_ratio=0.15, filename="icon-512-maskable.png")
