"""Generate assets/app.ico and assets/logo_*.png without third-party libraries.
The mark is a magenta-to-orange rounded tile with a white clock face. Run: python make_assets.py"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent / 'assets'
TOP, BOTTOM = (0xC1, 0x3A, 0xA8), (0xF5, 0x7C, 0x2B)
SAMPLES = 4


def inside_tile(x, y, size):
    inset = size * 0.03; radius = size * 0.22
    lo, hi = inset, size - inset
    if not (lo <= x <= hi and lo <= y <= hi): return False
    cx = min(max(x, lo + radius), hi - radius); cy = min(max(y, lo + radius), hi - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2


def inside_clock(x, y, size):
    """A ring with two hands (ten past ten), in the tile's units."""
    cx = cy = size / 2; r = size * 0.30; ring = size * 0.055
    d = math.hypot(x - cx, y - cy)
    if abs(d - r) <= ring / 2: return True
    def hand(angle_deg, length, width):
        a = math.radians(angle_deg - 90); ex, ey = cx + math.cos(a) * length, cy + math.sin(a) * length
        dx, dy = ex - cx, ey - cy; t = max(0, min(1, ((x - cx) * dx + (y - cy) * dy) / (dx * dx + dy * dy)))
        return math.hypot(x - cx - t * dx, y - cy - t * dy) <= width / 2
    return hand(60, r * 0.62, ring) or hand(-30, r * 0.85, ring) or d <= ring * 0.8


def render(size):
    rows = []
    for py in range(size):
        row = bytearray([0])  # PNG filter type 0
        for px in range(size):
            tile = mark = 0
            for sy in range(SAMPLES):
                for sx in range(SAMPLES):
                    x = px + (sx + .5) / SAMPLES; y = py + (sy + .5) / SAMPLES
                    if inside_tile(x, y, size):
                        tile += 1
                        if inside_clock(x, y, size): mark += 1
            total = SAMPLES * SAMPLES
            t = (px + py) / (2 * size)
            base = [round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)]
            mix = mark / tile if tile else 0
            rgb = [round(c + (255 - c) * mix) for c in base]
            row += bytes(rgb + [round(255 * tile / total)])
        rows.append(bytes(row))
    return png(size, b''.join(rows))


def png(size, raw):
    def chunk(kind, data): return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xFFFFFFFF)
    header = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    return b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', header) + chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b'')


def ico(images):
    head = struct.pack('<HHH', 0, 1, len(images)); offset = 6 + 16 * len(images); entries = b''; blobs = b''
    for size, data in images:
        entries += struct.pack('<BBBBHHII', size % 256, size % 256, 0, 0, 1, 32, len(data), offset)
        offset += len(data); blobs += data
    return head + entries + blobs


ICNS_TYPES = {16: b'icp4', 32: b'icp5', 64: b'icp6', 128: b'ic07', 256: b'ic08', 512: b'ic09'}


def icns(images):
    """Apple icon file with PNG entries (macOS 10.7 and later read these)."""
    body = b''.join(ICNS_TYPES[size] + struct.pack('>I', 8 + len(data)) + data for size, data in images if size in ICNS_TYPES)
    return b'icns' + struct.pack('>I', 8 + len(body)) + body


if __name__ == '__main__':
    OUT.mkdir(exist_ok=True)
    images = [(s, render(s)) for s in (16, 20, 24, 32, 40, 48, 64, 128, 256, 512)]
    (OUT / 'app.ico').write_bytes(ico([i for i in images if i[0] in (16, 20, 24, 32, 40, 48, 64, 256)]))
    (OUT / 'app.icns').write_bytes(icns(images))
    for s, data in images:
        if s in (24, 32, 48, 256): (OUT / f'logo_{s}.png').write_bytes(data)
    print('wrote', sorted(p.name for p in OUT.iterdir()))
