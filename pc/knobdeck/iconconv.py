"""Convert any image to the deck's icon format (KDI1: 48x48 RGB565 + 8-bit alpha, little endian)."""
import hashlib
import io
import struct

from PIL import Image

ICON_SIZE = 48


def to_kdi(img: Image.Image, size: int = ICON_SIZE, circle: bool = False) -> bytes:
    img = img.convert("RGBA")
    if circle:                     # crop to a centred square and round it off (avatars)
        side = min(img.size)
        img = img.crop(((img.width - side) // 2, (img.height - side) // 2,
                        (img.width + side) // 2, (img.height + side) // 2)).resize((size * 4, size * 4), Image.LANCZOS)
        mask = Image.new("L", img.size, 0)
        from PIL import ImageDraw
        ImageDraw.Draw(mask).ellipse((0, 0, img.width - 1, img.height - 1), fill=255)
        alpha = Image.composite(img.getchannel("A"), mask, mask)
        img.putalpha(alpha)
    # fit inside the square, keep aspect ratio, centre it
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    px = canvas.load()
    color = bytearray()
    alpha = bytearray()
    for y in range(size):
        for x in range(size):
            r, g, b, a = px[x, y]
            v = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            color += struct.pack("<H", v)
            alpha.append(a)
    return b"KDI1" + struct.pack("<HH", size, size) + bytes(color) + bytes(alpha)


def kdi_from_file(path: str) -> bytes:
    with Image.open(path) as im:
        if getattr(im, "n_frames", 1) > 1:
            im.seek(0)
        return to_kdi(im)


def kdi_from_bytes(data: bytes, circle: bool = False) -> bytes:
    with Image.open(io.BytesIO(data)) as im:
        return to_kdi(im, circle=circle)


def icon_hash(kdi: bytes) -> str:
    """Content address used as the file name on the device (10 hex chars)."""
    return hashlib.sha1(kdi).hexdigest()[:10]


def kdi_to_png(kdi: bytes) -> bytes:
    """Decode back to PNG (for previews in the settings window)."""
    w, h = struct.unpack("<HH", kdi[4:8])
    color, alpha = kdi[8:8 + w * h * 2], kdi[8 + w * h * 2:]
    im = Image.new("RGBA", (w, h))
    px = im.load()
    for i in range(w * h):
        v = color[2 * i] | color[2 * i + 1] << 8
        r, g, b = (v >> 11) << 3, ((v >> 5) & 0x3F) << 2, (v & 0x1F) << 3
        px[i % w, i // w] = (r, g, b, alpha[i])
    out = io.BytesIO()
    im.save(out, "PNG")
    return out.getvalue()
