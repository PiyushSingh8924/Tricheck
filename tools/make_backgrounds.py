"""Make the two home-page background images from your original photo.

    pip install pillow
    python3 tools/make_backgrounds.py campus.jpg

Writes static/img/campus-1600.jpg (under 600 KB) and static/img/campus-3840.jpg (under 1.5 MB).
The quality is lowered step by step until each file fits its size limit.
"""
import io, os, sys
from PIL import Image, ImageOps

SIZES = [(1600, 600_000, "campus-1600.jpg"), (3840, 1_500_000, "campus-3840.jpg")]

def save_under(img, path, limit):
    for q in range(88, 34, -3):
        buf = io.BytesIO(); img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
        if buf.tell() <= limit: break
    open(path, "wb").write(buf.getvalue())
    print(f"{path}: {img.width}x{img.height}, quality {q}, {buf.tell() // 1000} KB")
    if buf.tell() > limit: sys.exit(f"Could not get {path} under {limit // 1000} KB - try a less detailed photo.")

if len(sys.argv) != 2: sys.exit(__doc__)
src = ImageOps.exif_transpose(Image.open(sys.argv[1])).convert("RGB")
if src.width < 3840: sys.exit(f"The photo is only {src.width}px wide. Use one that is at least 3840px wide.")
out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "static", "img"); os.makedirs(out, exist_ok=True)
for width, limit, name in SIZES:
    save_under(src.resize((width, round(src.height * width / src.width)), Image.LANCZOS), os.path.join(out, name), limit)
