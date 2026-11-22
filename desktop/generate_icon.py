"""Keel's icons. The app icon is the ink-wash artwork in assets/KeelArtwork.png (generated for
Keel, a rising brushstroke and a vermilion sun), fitted to the macOS icon grid here; the
menu-bar marks are drawn: black alpha masks so macOS can adapt them to either theme, using the
same 32-unit geometry as KeelMark in apple/Sources/Keel/Theme.swift.
"""

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


def _curve(start, control1, control2, end):
    return [
        tuple(
            (1 - t) ** 3 * start[axis]
            + 3 * (1 - t) ** 2 * t * control1[axis]
            + 3 * (1 - t) * t**2 * control2[axis]
            + t**3 * end[axis]
            for axis in (0, 1)
        )
        for t in (i / 30 for i in range(31))
    ]


def _mark(draw, scale, offset, ink, fin, wave):
    def points(coords):
        return [(offset[0] + x * scale, offset[1] + y * scale) for x, y in coords]

    hull = [(3, 13), (29, 13), (25.8, 19.2)]
    hull += _curve((25.8, 19.2), (25.3, 20.2), (24.3, 20.8), (23.1, 20.8))
    hull += [(8.9, 20.8)]
    hull += _curve((8.9, 20.8), (7.7, 20.8), (6.7, 20.2), (6.2, 19.2))
    draw.polygon(points(hull), fill=ink)
    draw.polygon(
        points([(14.5, 20.8), (17.5, 20.8), (16.9, 28.5), (15.1, 28.5)]), fill=fin
    )
    wave_points = []
    for index in range(4):
        x = 2 + index * 6.6
        direction = 1 if index % 2 == 0 else -1
        wave_points += _curve(
            (x, 23.5),
            (x + 2.2, 23.5 + direction * 1.3),
            (x + 4.4, 23.5 + direction * 1.3),
            (x + 6.6, 23.5),
        )
    draw.line(points(wave_points), fill=wave, width=max(1, round(scale * 1.3)))
    radius = scale * 0.65
    for x, y in points(wave_points):
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=wave)


ARTWORK = Path(__file__).parent / "assets" / "KeelArtwork.png"
GRID_TILE = 824  # macOS icon grid: an 824-px tile centred on the 1024 canvas


def app_icon():
    """The artwork with its tile scaled to the macOS grid, so it sits level with other icons."""
    art = Image.open(ARTWORK).convert("RGBA")
    solid = art.getchannel("A").point(lambda v: 255 if v > 250 else 0).getbbox()
    assert solid, "artwork has no opaque tile"
    left, top, right, bottom = solid
    scale = GRID_TILE / (right - left)
    size = round(art.width * scale)
    art = art.resize((size, size), Image.Resampling.LANCZOS)
    cx = (left + right) / 2 * scale
    cy = (top + bottom) / 2 * scale
    placed = Image.new("RGBA", (1024, 1024))
    placed.alpha_composite(art, (round(512 - cx), round(512 - cy)))
    # Keep only the tile: generated art carries faint noise and a baked shadow outside it.
    alpha = placed.getchannel("A")
    tile = alpha.point(lambda v: 255 if v > 200 else 0).filter(ImageFilter.GaussianBlur(0.8))
    placed.putalpha(ImageChops.multiply(alpha.point(lambda v: 255 if v > 200 else v), tile))
    shadow = Image.new("RGBA", (1024, 1024))
    shadow.putalpha(tile.point(lambda v: v * 60 // 255).filter(ImageFilter.GaussianBlur(14)))
    canvas = Image.new("RGBA", (1024, 1024))
    canvas.alpha_composite(shadow, (0, 10))
    canvas.alpha_composite(placed)
    return canvas


def tray_icon(pending=False):
    image = Image.new("RGBA", (176, 176))
    draw = ImageDraw.Draw(image)
    _mark(draw, 5.6, (2, -28), "black", "black", "black")
    if pending:
        draw.ellipse((136, 12, 168, 44), fill="black")
    return image.resize((44, 44), Image.Resampling.LANCZOS)


def main() -> None:
    output = Path(__file__).parent / "assets"
    output.mkdir(exist_ok=True)
    image = app_icon()
    image.save(output / "Keel.png")
    image.save(output / "Keel.icns", format="ICNS")
    tray_icon().save(output / "KeelTray.png")
    tray_icon(True).save(output / "KeelTrayPending.png")
    print(f"Icon: {output / 'Keel.icns'}")


if __name__ == "__main__":
    main()
