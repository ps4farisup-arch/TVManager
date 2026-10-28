# -*- coding: utf-8 -*-
"""
مناظر سعودية مرسومة (رسومات أصلية، بدون صور محمية):
تراث: الدرعية، العلا (الحِجر)، جدة التاريخية، حافة العالم
رؤية 2030: أفق الرياض، نيوم (ذا لاين)، البحر الأحمر

كل منظر طبقات (بعيدة/وسط/قريبة) تتلوّن حسب وقت الصلاة، وتضيء نوافذها بالليل.
الأصناف: f1 بعيد، f2 وسط، f3 قريب، sea بحر، lt أضواء، st نجوم، rf انعكاس، mr مرآة، cv نقش.
"""

import math
import random

W, H = 1920, 1080


def _p(pts):
    return "M" + " L".join("%.0f,%.0f" % (x, y) for x, y in pts) + " Z"


def _path(d, cls, extra=""):
    return '<path class="%s" d="%s"%s/>' % (cls, d, extra)


def _rect(x, y, w, h, cls):
    return '<rect class="%s" x="%.0f" y="%.0f" width="%.0f" height="%.0f"/>' % (cls, x, y, w, h)


def _ridge(rng, x0, x1, base, lo, hi, step, flat=False):
    """خط جبال/كثبان من x0 إلى x1، يُغلق إلى أسفل الشاشة."""
    pts = [(x0, H), (x0, base)]
    x = x0
    while x < x1:
        x += rng.uniform(step * 0.6, step * 1.4)
        y = rng.uniform(lo, hi)
        if flat:
            pts.append((x, y))
            x += rng.uniform(step * 0.5, step)
            pts.append((x, y))
        else:
            pts.append((x, y))
    pts += [(x1, base), (x1, H)]
    return _p(pts)


def _palm(x, g, h, cls, lean=0.0):
    tx, ty = x + lean, g - h
    out = ["M%.0f,%.0f Q%.0f,%.0f %.0f,%.0f L%.0f,%.0f Q%.0f,%.0f %.0f,%.0f Z" % (
        x - 7, g, x + lean * 0.3 - 5, g - h * 0.5, tx - 3, ty, tx + 3, ty, x + lean * 0.3 + 5, g - h * 0.5, x + 7, g)]
    L = h * 0.42
    for ang, droop in ((200, .55), (170, .35), (140, .15), (105, 0), (70, .1), (35, .3), (5, .5), (-20, .6)):
        a = math.radians(ang)
        ex, ey = tx + L * math.cos(a), ty - L * math.sin(a) + L * droop
        mx, my = (tx + ex) / 2, (ty + ey) / 2
        nx, ny = -(ey - ty), (ex - tx)
        n = math.hypot(nx, ny) or 1
        k = L * 0.16
        out.append("M%.0f,%.0f Q%.0f,%.0f %.0f,%.0f Q%.0f,%.0f %.0f,%.0f Z" % (
            tx, ty, mx + nx / n * k, my + ny / n * k - L * 0.12, ex, ey, mx - nx / n * k * 0.3, my - ny / n * k * 0.3, tx, ty))
    return _path(" ".join(out), cls)


def _najdi(rng, x, w, h, g, cls, lights, tower=False):
    """مبنى نجدي من الطين: شرفات مثلثة في الأعلى وفتحات تهوية مثلثة."""
    top = g - h
    inset = w * 0.12 if tower else 0
    pts = [(x, g), (x + inset, top)]
    step = 16
    n = max(2, int((w - 2 * inset) // step))
    sw = (w - 2 * inset) / n
    for i in range(n):
        pts.append((x + inset + i * sw + sw / 2, top - 13))
        pts.append((x + inset + (i + 1) * sw, top))
    pts.append((x + w, g))
    d = _p(pts)
    holes = []
    row_y = top + 26
    hx = x + inset + 14
    while hx + 10 < x + w - inset - 6:
        holes.append(_p([(hx, row_y + 11), (hx + 5, row_y), (hx + 10, row_y + 11)]))
        hx += 24
    d += " " + " ".join(holes)
    out = [_path(d, cls, ' fill-rule="evenodd"')]
    for _ in range(int(w // 45)):
        if rng.random() < 0.55:
            wx = rng.uniform(x + inset + 8, x + w - inset - 20)
            wy = rng.uniform(top + 50, g - 40)
            lights.append(_rect(wx, wy, 9, 13, "lt"))
    return "".join(out)


def scene_diriyah(rng):
    out, lights = [], []
    out.append(_path(_ridge(rng, -20, W + 20, 900, 860, 890, 140), "f1"))
    for i in range(26):
        x = rng.uniform(0, W)
        out.append(_palm(x, 905, rng.uniform(110, 190), "f1", rng.uniform(-18, 18)))
    x = 120
    while x < W - 120:
        w = rng.uniform(90, 190)
        tower = rng.random() < 0.3
        h = rng.uniform(230, 320) if tower else rng.uniform(110, 210)
        out.append(_najdi(rng, x, w if not tower else w * 0.6, h, 975, "f2", lights, tower))
        x += (w if not tower else w * 0.6) + rng.uniform(-10, 25)
    out.extend(lights)
    lights = []
    out.append(_najdi(rng, -30, 260, 330, 1080, "f3", lights, True))
    out.append(_najdi(rng, 200, 420, 150, 1080, "f3", lights))
    out.append(_najdi(rng, 1380, 360, 170, 1080, "f3", lights))
    out.append(_najdi(rng, 1700, 260, 380, 1080, "f3", lights, True))
    out.append(_rect(-10, 1040, W + 20, 50, "f3"))
    out.extend(lights)
    return "".join(out)


def scene_alula(rng):
    out = []
    out.append(_path(_ridge(rng, -20, W + 20, 720, 600, 690, 120, flat=True), "f1"))
    out.append(_path("M-20,1080 L-20,700 Q60,560 180,590 Q260,520 340,610 Q420,640 470,760 L520,1080 Z", "f2"))
    out.append(_path("M1450,1080 L1480,760 Q1540,600 1650,630 Q1760,560 1840,650 Q1920,700 1940,760 L1940,1080 Z", "f2"))
    # الصخرة الكبيرة وواجهتها المنحوتة
    out.append(_path("M740,1080 L772,830 Q752,705 818,642 L848,566 Q898,522 962,544 L1012,502 Q1082,468 1152,507 L1212,482 Q1292,500 1322,582 Q1374,646 1360,766 L1402,1080 Z", "f3"))
    fx, fy, fw, fh = 1035, 640, 130, 300
    steps = []
    s = 11
    for side in (0, 1):
        x0 = fx if side == 0 else fx + fw
        sign = 1 if side == 0 else -1
        pts = [(x0, fy)]
        cx, cy = x0, fy
        for _ in range(5):
            cy -= s
            pts.append((cx, cy))
            cx += sign * s
            pts.append((cx, cy))
        pts.append((cx, fy))
        steps.append(_p(pts))
    out.append(_path(" ".join(steps) + " " + _p([(fx, fy), (fx + fw, fy), (fx + fw, fy + fh), (fx, fy + fh)]), "cv"))
    out.append(_path(_p([(fx + 38, fy + 150), (fx + 65, fy + 120), (fx + 92, fy + 150)]), "f3"))
    out.append(_rect(fx + 45, fy + 165, 40, 135, "f3"))
    out.append(_rect(fx + 12, fy + 20, 6, fh - 20, "f3"))
    out.append(_rect(fx + fw - 18, fy + 20, 6, fh - 20, "f3"))
    out.append(_rect(fx - 6, fy + 70, fw + 12, 6, "f3"))
    out.append(_path(_ridge(rng, -20, W + 20, 1030, 1000, 1030, 200), "f3"))
    return "".join(out)


def scene_jeddah(rng):
    out, lights, lattice = [], [], []
    out.append(_path(_ridge(rng, -20, W + 20, 940, 900, 935, 90), "f1"))
    out.append(_rect(1180, 700, 26, 240, "f1"))
    out.append(_path("M1172,700 L1193,640 L1214,700 Z", "f1"))
    x = 160
    while x < W - 140:
        w = rng.uniform(130, 190)
        h = rng.uniform(230, 360)
        top = 1000 - h
        parts = [_p([(x, 1000), (x, top), (x + w, top), (x + w, 1000)])]
        for i in range(int(w // 22)):
            parts.append(_p([(x + i * 22 + 4, top), (x + i * 22 + 4, top - 12), (x + i * 22 + 14, top - 12), (x + i * 22 + 14, top)]))
        floors = int((h - 60) // 85)
        for f in range(floors):
            ry = top + 30 + f * 85
            rw = w * rng.uniform(0.4, 0.62)
            rx = x + (w - rw) / 2
            parts.append(_p([(rx - 8, ry), (rx + rw + 8, ry), (rx + rw + 8, ry + 62), (rx - 8, ry + 62)]))
            for k in range(1, 6):
                lattice.append(_rect(rx - 4 + k * (rw + 8) / 6, ry + 6, 2, 50, "rf"))
            if rng.random() < 0.6:
                lights.append(_rect(rx + rw * 0.28, ry + 18, rw * 0.14, 26, "lt"))
                lights.append(_rect(rx + rw * 0.58, ry + 18, rw * 0.14, 26, "lt"))
        out.append(_path(" ".join(parts), "f2"))
        x += w + rng.uniform(0, 8)
    out.extend(lattice)
    out.extend(lights)
    out.append(_rect(-10, 1000, W + 20, 90, "f3"))
    for lx in range(120, W, 300):
        out.append(_rect(lx, 900, 6, 100, "f3"))
        out.append('<circle class="lt" cx="%d" cy="896" r="9"/>' % (lx + 3))
    return "".join(out)


def _tower(x, w, h, g, cls, slant=0):
    return _path(_p([(x, g), (x, g - h + max(0, slant)), (x + w, g - h + max(0, -slant)), (x + w, g)]), cls)


def scene_riyadh(rng):
    out, lights = [], []
    x = -20
    while x < W:
        w = rng.uniform(40, 110)
        h = rng.uniform(50, 170)
        out.append(_tower(x, w, h, 970, "f1"))
        x += w + rng.uniform(2, 14)
    # مركز الملك عبدالله المالي (يسار) وأبراج متنوعة
    for (bx, bw, bh, sl) in ((250, 70, 380, 40), (330, 60, 300, -30), (400, 85, 430, 25), (150, 60, 250, 0),
                             (1280, 70, 260, 20), (1360, 55, 220, -25), (1430, 80, 300, 0), (1530, 60, 200, 15)):
        out.append(_tower(bx, bw, bh, 1000, "f2", sl))
        for r in range(int(bh // 34)):
            if rng.random() < 0.5:
                lights.append(_rect(bx + 8, 1000 - bh + 40 + r * 30, bw - 16, 3, "lt"))
    # الفيصلية
    cx = 610
    out.append(_path(_p([(cx - 58, 1000), (cx - 8, 500), (cx + 8, 500), (cx + 58, 1000)]), "f2"))
    out.append(_rect(cx - 2, 410, 4, 95, "f2"))
    out.append('<circle class="gl" cx="%d" cy="620" r="24"/>' % cx)
    # برج المملكة
    cx = 900
    out.append(_path("M%d,1000 L%d,640 Q%d,370 %d,265 L%d,265 Q%d,960 %d,265 L%d,265 Q%d,370 %d,640 L%d,1000 Z" % (
        cx - 66, cx - 61, cx - 58, cx - 30, cx - 24, cx, cx + 24, cx + 30, cx + 58, cx + 61, cx + 66), "f2"))
    out.append(_rect(cx - 27, 330, 54, 12, "f2"))
    for r in range(18):
        lights.append(_rect(cx - 42, 620 + r * 20, 84, 2, "lt"))
    out.extend(lights)
    out.append(_rect(-10, 1000, W + 20, 90, "f3"))
    out.append(_rect(-10, 1018, W + 20, 3, "lt"))
    out.append(_rect(-10, 1040, W + 20, 2, "lt"))
    return "".join(out)


def scene_line(rng):
    out = ['<defs><linearGradient id="mirg" x1="0" y1="0" x2="0" y2="1">'
           '<stop offset="0" stop-color="#ffffff" stop-opacity=".55"/>'
           '<stop offset=".55" stop-color="#ffffff" stop-opacity=".12"/>'
           '<stop offset="1" stop-color="#ffffff" stop-opacity=".3"/></linearGradient></defs>']
    out.append(_path(_ridge(rng, 700, W + 20, 760, 560, 700, 70), "f1"))
    vx, top_l, top_r, bot_l, bot_r = 1660, 430, 712, 960, 728
    out.append(_path(_p([(-20, top_l), (vx, top_r), (vx, bot_r), (-20, bot_l)]), "f2"))
    out.append('<path class="mr" fill="url(#mirg)" d="%s"/>' % _p([(-20, top_l), (vx, top_r), (vx, bot_r), (-20, bot_l)]))
    t = 0.0
    while t < 0.985:
        x = -20 + (vx + 20) * t
        yt = top_l + (top_r - top_l) * t
        yb = bot_l + (bot_r - bot_l) * t
        out.append('<path class="rf" d="M%.0f,%.0f L%.0f,%.0f" stroke="#fff" stroke-width="%.1f"/>' % (x, yt, x, yb, max(0.6, 3 * (1 - t))))
        t += 0.07 * (1 - t) + 0.008
    out.append(_path("M-20,1080 L-20,960 Q300,900 640,950 Q980,1000 1300,930 Q1600,870 1940,920 L1940,1080 Z", "f3"))
    return "".join(out)


def scene_redsea(rng):
    out = []
    hz = 770
    out.append(_rect(-10, hz, W + 20, H - hz + 10, "sea"))
    out.append(_path("M300,%d Q380,%d 470,%d Z M1220,%d Q1300,%d 1390,%d Z M1600,%d Q1660,%d 1730,%d Z" % (
        hz, hz - 22, hz, hz, hz - 16, hz, hz, hz - 12, hz), "f1"))
    for _ in range(90):
        y = rng.uniform(hz + 6, H)
        w = rng.uniform(20, 120) * (y - hz) / (H - hz) + 8
        out.append(_rect(rng.uniform(0, W), y, w, 2, "rf"))
    out.append(_path("M-20,1080 L-20,860 Q120,800 300,820 Q480,840 620,900 L700,1080 Z", "f2"))
    for (px, ph, ln) in ((90, 210, -15), (170, 260, 12), (260, 190, 20), (360, 230, -8), (470, 170, 15)):
        out.append(_palm(px, 850, ph, "f2", ln))
    walk_y = 872
    out.append(_rect(820, walk_y, 1000, 7, "f2"))
    for i in range(9):
        vx = 860 + i * 110
        out.append(_rect(vx, walk_y - 8, 80, 12, "f2"))
        out.append(_path("M%d,%d Q%d,%d %d,%d Z" % (vx + 2, walk_y - 8, vx + 40, walk_y - 70, vx + 78, walk_y - 8), "f2"))
        for sx in (vx + 10, vx + 66):
            out.append(_rect(sx, walk_y + 4, 4, 40, "f2"))
        out.append(_rect(vx + 28, walk_y - 32, 24, 18, "lt"))
        out.append(_rect(vx + 30, walk_y + 46, 20, 3, "lt"))
    out.append(_rect(1810, walk_y - 4, 8, 40, "f2"))
    return "".join(out)


def scene_edge(rng):
    out = []
    out.append(_path(_ridge(rng, -20, W + 20, 800, 780, 798, 160), "f1"))
    out.append(_path(_ridge(rng, 1150, W + 20, 800, 690, 740, 90, flat=True), "f1"))
    pts = [(-20, H), (-20, 600)]
    x = -20
    while x < 1060:
        x += rng.uniform(40, 90)
        pts.append((x, rng.uniform(585, 615)))
    for (dx, dy) in ((20, 660), (40, 700), (30, 760), (55, 800), (40, 830), (90, 860)):
        x += dx
        pts.append((x, dy + rng.uniform(-10, 10)))
    pts += [(W + 20, 880), (W + 20, H)]
    out.append(_path(_p(pts), "f2"))
    pts = [(-20, H), (-20, 790)]
    x = -20
    while x < 640:
        x += rng.uniform(30, 70)
        pts.append((x, rng.uniform(770, 800)))
    edge_x, edge_y = x, pts[-1][1]
    for (dx, dy) in ((15, 860), (25, 920), (10, 990), (30, 1040), (20, 1090)):
        x += dx
        pts.append((x, dy))
    out.append(_path(_p(pts), "f3"))
    px, py = edge_x - 30, edge_y
    out.append('<circle class="f3" cx="%.0f" cy="%.0f" r="7"/>' % (px, py - 44))
    out.append(_path(_p([(px - 7, py), (px - 6, py - 36), (px + 6, py - 36), (px + 7, py)]), "f3"))
    return "".join(out)


SCENES = [
    ("الدرعية", "حي الطريف", "تراث", scene_diriyah),
    ("الرياض", "أفق العاصمة", "رؤية ٢٠٣٠", scene_riyadh),
    ("العلا", "الحِجر", "تراث", scene_alula),
    ("نيوم", "ذا لاين", "رؤية ٢٠٣٠", scene_line),
    ("جدة التاريخية", "البلد", "تراث", scene_jeddah),
    ("البحر الأحمر", "الجزر والمنتجعات", "رؤية ٢٠٣٠", scene_redsea),
    ("جبال طويق", "حافة العالم", "طبيعة", scene_edge),
]


def stars(seed=7):
    rng = random.Random(seed)
    return "".join('<circle class="st" cx="%.0f" cy="%.0f" r="%.1f"/>' % (
        rng.uniform(0, W), rng.uniform(0, 640), rng.choice((0.8, 1.1, 1.5, 2.0))) for _ in range(140))


def build():
    """يرجّع قائمة: [مكان، تفاصيل، تصنيف، svg]."""
    out = []
    for i, (place, sub, tag, fn) in enumerate(SCENES):
        rng = random.Random(1000 + i)
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" preserveAspectRatio="xMidYMax slice">'
               '%s</svg>' % (W, H, fn(rng)))
        out.append([place, sub, tag, svg])
    return out
