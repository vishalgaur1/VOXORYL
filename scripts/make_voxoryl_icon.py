"""Generate Voxoryl wireframe-orb icons (favicon + Windows .ico)."""
from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

OUT = Path(__file__).resolve().parents[1] / "voxoryl" / "static"


def draw_orb(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pad = max(1, size // 16)
    bg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    bg_draw = ImageDraw.Draw(bg)
    radius = max(2, size // 5)
    bg_draw.rounded_rectangle(
        [pad, pad, size - pad - 1, size - pad - 1],
        radius=radius,
        fill=(12, 16, 28, 255),
    )
    img = Image.alpha_composite(img, bg)

    cx = cy = size / 2
    r = size * 0.34

    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    for i, a in enumerate((40, 28, 16)):
        rr = r + size * (0.08 + i * 0.04)
        gdraw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=(120, 190, 255, a))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(1, size // 18)))
    img = Image.alpha_composite(img, glow)
    draw = ImageDraw.Draw(img)

    stroke = max(1, size // 28)
    color = (230, 240, 255, 255)
    dim = (160, 200, 255, 180)

    draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline=color, width=stroke)

    for t in (-0.55, -0.25, 0.0, 0.25, 0.55):
        y = cy + r * t
        half = math.sqrt(max(0.0, 1 - t * t)) * r
        squash = 0.22 + 0.18 * (1 - abs(t))
        bbox = [cx - half, y - half * squash, cx + half, y + half * squash]
        draw.ellipse(bbox, outline=dim if abs(t) > 0.4 else color, width=max(1, stroke))

    for frac in (-0.75, -0.4, 0.0, 0.4, 0.75):
        w = max(stroke * 2, r * math.sqrt(max(0.05, 1 - frac * frac)) * 0.92)
        bbox = [cx - w, cy - r, cx + w, cy + r]
        col = color if abs(frac) < 0.05 else dim
        draw.ellipse(bbox, outline=col, width=max(1, stroke))

    hi = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    hdraw = ImageDraw.Draw(hi)
    hdraw.arc(
        [cx - r + 1, cy - r + 1, cx + r - 1, cy + r - 1],
        start=200,
        end=290,
        fill=(255, 255, 255, 220),
        width=max(1, stroke),
    )
    img = Image.alpha_composite(img, hi)
    return img


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sizes = [16, 24, 32, 48, 64, 128, 256]
    images = [draw_orb(s) for s in sizes]
    draw_orb(256).save(OUT / "voxoryl-icon.png")
    draw_orb(32).save(OUT / "favicon-32.png")
    draw_orb(16).save(OUT / "favicon-16.png")
    # Pillow ICO: pass the largest frame + sizes= so Windows gets 16/32/48/256 entries.
    # (append_images alone only embeds the first frame.)
    images[-1].save(OUT / "voxoryl.ico", format="ICO", sizes=[(s, s) for s in sizes])
    images[2].save(OUT / "favicon.ico", format="ICO", sizes=[(16, 16), (32, 32), (48, 48)])
    (OUT / "voxoryl-icon.svg").write_text(
        """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">
  <rect x="4" y="4" width="56" height="56" rx="14" fill="#0c101c"/>
  <circle cx="32" cy="32" r="18" stroke="#e6f0ff" stroke-width="1.8"/>
  <ellipse cx="32" cy="32" rx="18" ry="6.5" stroke="#a0c8ff" stroke-width="1.2"/>
  <ellipse cx="32" cy="32" rx="18" ry="12.5" stroke="#c8dcff" stroke-width="1.1"/>
  <ellipse cx="32" cy="32" rx="7" ry="18" stroke="#a0c8ff" stroke-width="1.2"/>
  <ellipse cx="32" cy="32" rx="13" ry="18" stroke="#c8dcff" stroke-width="1.1"/>
  <path d="M18 24a18 18 0 0 1 28-6" stroke="#fff" stroke-width="1.4" stroke-linecap="round" opacity=".85"/>
</svg>
""",
        encoding="utf-8",
    )
    print("ok", OUT / "voxoryl.ico")


if __name__ == "__main__":
    main()
