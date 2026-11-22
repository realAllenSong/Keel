"""Generated artwork is self-contained and has separate menu-bar pending states."""

import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "generate_icon", Path(__file__).resolve().parents[1] / "generate_icon.py"
)
assert spec is not None and spec.loader is not None
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)


def test_app_icon_fits_the_macos_grid_with_ink_and_vermilion():
    image = icons.app_icon()
    assert image.size == (1024, 1024)
    assert image.getpixel((0, 0))[3] == 0
    tile = image.getchannel("A").point(lambda v: 255 if v > 250 else 0).getbbox()
    assert tile is not None
    left, _, right, _ = tile
    assert abs((right - left) - 824) <= 6 and abs((left + right) / 2 - 512) <= 2
    pixels = [p for p in image.getdata() if p[3] > 240]
    assert any(r < 60 and g < 60 and b < 60 for r, g, b, _ in pixels)  # the ink stroke
    assert any(
        r > 170 and g < 90 and b < 70 for r, g, b, _ in pixels
    )  # the vermilion sun


def test_pending_tray_icon_adds_a_dot_without_changing_the_boat():
    normal = icons.tray_icon(False)
    pending = icons.tray_icon(True)
    assert normal.size == pending.size == (44, 44)
    assert normal.getpixel((38, 7))[3] == 0
    assert pending.getpixel((38, 7)) == (0, 0, 0, 255)
    assert (
        normal.crop((0, 17, 44, 44)).tobytes()
        == pending.crop((0, 17, 44, 44)).tobytes()
    )
    for image in (normal, pending):
        assert all(
            r == g == b == 0
            for r, g, b, alpha in (
                image.getpixel((x, y)) for x in range(44) for y in range(44)
            )
            if alpha
        )
