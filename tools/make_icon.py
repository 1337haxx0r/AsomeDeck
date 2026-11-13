"""Build the app icon from the source artwork.

Kept as a tool rather than done once by hand, because the inputs are judgement calls that
somebody will want to revisit: how much of the white canvas to trim, how much breathing
room to give it back, and how round the corners are.

Three things it does that a straight resize does not:

* **Trims to the artwork.** The source sits off-centre on a white canvas -- 24px of margin
  on the left, 65 on the right, 47 above and 7 below. Scaled as-is the icon would look
  lopsided and too small in a 16px taskbar slot.
* **Squares it deliberately.** The artwork is 491x369, so the square has to come from
  somewhere; padding it symmetrically keeps the composition centred rather than cropping
  the design.
* **Rounds the corners into transparency.** The background is white, and a white square is
  a poor neighbour on a dark taskbar. A rounded square reads as a deliberate app icon
  instead of an untrimmed screenshot.

The .ico is written here rather than through QImageWriter, which emits a single size. A
multi-size file matters: Windows picks 16px for the title bar and 256px for the shell's
extra-large view, and letting it downscale one big bitmap to 16px produces mush.

On macOS it also writes the .icns the .app bundle needs, from the same master, so the two
platforms' icons cannot drift apart. That one goes through ``iconutil``, Apple's own tool,
rather than being packed by hand the way the .ico is -- the format has a container per
retina variant and no reason to reimplement.

Run:  .venv\\Scripts\\python.exe tools\\make_icon.py <source.png>      (Windows)
      .venv/bin/python tools/make_icon.py <source.png>              (macOS)
"""

from __future__ import annotations

import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath
from PySide6.QtWidgets import QApplication

#: Sizes Windows actually asks for. 16 and 32 are the common ones (title bar, taskbar,
#: Explorer list); 256 is the extra-large view and the one used in installers.
ICO_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)

#: Everything at or above this on all three channels counts as the canvas, not the art.
WHITE = 244

#: Padding added back after trimming, as a fraction of the square's side. Icons that touch
#: their own edge look cramped once the OS puts them next to other icons.
MARGIN = 0.07

#: Corner radius as a fraction of the side.
RADIUS = 0.18

#: Below this, the icon is drawn from a tighter master. At 16px the margin and the corner
#: radius together eat most of the tile before the artwork gets any of it, and what
#: survives is unreadable -- the design is fine detail on white, which is the worst case
#: for downscaling. Filling more of the tile keeps the one thing that still reads at that
#: size: the colour signature down the left edge.
SMALL_ABOVE = 40
SMALL_MARGIN = 0.015
SMALL_RADIUS = 0.13

#: The (point size, scale) pairs an .iconset must contain. Apple's naming is load-bearing:
#: iconutil reads the size and the @2x suffix out of the filename, so ``icon_32x32@2x.png``
#: has to be 64 real pixels. Getting a name wrong is not an error, it is a missing size.
ICNS_SIZES = ((16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
              (256, 1), (256, 2), (512, 1), (512, 2))

OUT_DIR = pathlib.Path(__file__).resolve().parent.parent / "assets" / "icon"


def content_bounds(img: QImage) -> tuple[int, int, int, int]:
    """The bounding box of everything that is not white canvas."""
    width, height = img.width(), img.height()
    min_x, min_y, max_x, max_y = width, height, -1, -1
    for y in range(height):
        for x in range(width):
            colour = img.pixelColor(x, y)
            if colour.alpha() < 8:
                continue
            if colour.red() > WHITE and colour.green() > WHITE and colour.blue() > WHITE:
                continue
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
    if max_x < 0:
        return 0, 0, width - 1, height - 1
    return min_x, min_y, max_x, max_y


def square_master(source: QImage, side: int = 1024, margin: float = MARGIN,
                  radius: float = RADIUS) -> QImage:
    """Trim, centre on a square, round the corners, and return it at ``side``."""
    img = source.convertToFormat(QImage.Format.Format_ARGB32)
    min_x, min_y, max_x, max_y = content_bounds(img)
    art = img.copy(min_x, min_y, max_x - min_x + 1, max_y - min_y + 1)

    inner = int(side * (1.0 - 2 * margin))
    scaled = art.scaled(inner, inner, Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation)

    master = QImage(side, side, QImage.Format.Format_ARGB32)
    master.fill(Qt.GlobalColor.transparent)

    painter = QPainter(master)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

    # The rounded square is the icon's silhouette; everything is drawn inside it.
    path = QPainterPath()
    path.addRoundedRect(QRectF(0, 0, side, side), side * radius, side * radius)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QBrush(QColor("#ffffff")))
    painter.drawPath(path)
    painter.setClipPath(path)

    painter.drawImage((side - scaled.width()) // 2, (side - scaled.height()) // 2, scaled)
    painter.end()
    return master


def png_bytes(img: QImage) -> bytes:
    # The QByteArray is bound to a name deliberately. QBuffer does not own the array it is
    # given, so passing a temporary lets Python collect it while the buffer still points
    # at it -- which takes the interpreter down without a traceback.
    store = QByteArray()
    buffer = QBuffer(store)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    img.save(buffer, "PNG")
    buffer.close()
    return bytes(store)


def write_ico(master: QImage, path: pathlib.Path, small: QImage | None = None,
              sizes=ICO_SIZES) -> None:
    """A multi-size .ico of PNG-compressed entries.

    PNG payloads rather than BMP: supported since Vista, and it keeps a 256px entry from
    costing 256KB of uncompressed bitmap.
    """
    frames = []
    for size in sizes:
        chosen = small if (small is not None and size <= SMALL_ABOVE) else master
        frame = chosen.scaled(size, size, Qt.AspectRatioMode.IgnoreAspectRatio,
                              Qt.TransformationMode.SmoothTransformation)
        frames.append((size, png_bytes(frame)))

    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = len(header) + 16 * len(frames)
    entries, payloads = b"", b""
    for size, data in frames:
        # 0 means 256 in a directory entry; the field is a single byte.
        entries += struct.pack("<BBBBHHII", size % 256, size % 256, 0, 0, 1, 32,
                               len(data), offset)
        payloads += data
        offset += len(data)

    path.write_bytes(header + entries + payloads)


def write_icns(master: QImage, path: pathlib.Path, small: QImage | None = None) -> bool:
    """Write an .icns via a temporary .iconset and ``iconutil``. False if it is unavailable.

    Not fatal when it fails. This tool's job on a Windows machine is the .ico, and there is
    no iconutil there to run -- returning False lets the caller say so and carry on rather
    than making the Windows icon depend on a Mac being present.
    """
    if not shutil.which("iconutil"):
        return False
    with tempfile.TemporaryDirectory() as tmp:
        iconset = pathlib.Path(tmp) / "asomedeck.iconset"
        iconset.mkdir()
        for points, scale in ICNS_SIZES:
            pixels = points * scale
            chosen = small if (small is not None and pixels <= SMALL_ABOVE) else master
            frame = chosen.scaled(pixels, pixels, Qt.AspectRatioMode.IgnoreAspectRatio,
                                  Qt.TransformationMode.SmoothTransformation)
            suffix = "" if scale == 1 else f"@{scale}x"
            frame.save(str(iconset / f"icon_{points}x{points}{suffix}.png"), "PNG")
        result = subprocess.run(
            ["iconutil", "--convert", "icns", "--output", str(path), str(iconset)],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            print(f"iconutil failed: {result.stderr.strip()}")
            return False
    return True


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    source_path = pathlib.Path(argv[1])
    if not source_path.is_file():
        print(f"no such file: {source_path}")
        return 1

    app = QApplication([])
    source = QImage(str(source_path))
    if source.isNull():
        print(f"could not read {source_path}")
        return 1

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    master = square_master(source)
    small = square_master(source, margin=SMALL_MARGIN, radius=SMALL_RADIUS)

    png_path = OUT_DIR / "asomedeck.png"
    ico_path = OUT_DIR / "asomedeck.ico"
    master.scaled(512, 512, Qt.AspectRatioMode.IgnoreAspectRatio,
                  Qt.TransformationMode.SmoothTransformation).save(str(png_path), "PNG")
    write_ico(master, ico_path, small=small)

    print(f"source {source.width()}x{source.height()} -> master {master.width()}px")
    print(f"  {png_path}  ({png_path.stat().st_size:,} bytes)")
    print(f"  {ico_path}  ({ico_path.stat().st_size:,} bytes, {len(ICO_SIZES)} sizes; "
          f"<={SMALL_ABOVE}px drawn tighter)")

    icns_path = OUT_DIR / "asomedeck.icns"
    if write_icns(master, icns_path, small=small):
        print(f"  {icns_path}  ({icns_path.stat().st_size:,} bytes, "
              f"{len(ICNS_SIZES)} variants)")
    else:
        print("  (no .icns: iconutil is macOS-only, so the bundle icon was not rebuilt)")
    _ = app
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
