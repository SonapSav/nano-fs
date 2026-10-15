"""The AW139 helicopter model for the viewer (flightsim/viewer/models/aw139.glb), built in
Blender from Leonardo's published three-view drawing and dimensions.

    blender --background --factory-startup --python scripts/blender/aw139.py

builds the model and writes the .glb (Blender 5.2; other versions may export slightly
different files). Inside an open Blender, exec() of this file builds the collection AW139
without exporting. Origin on the ground under the main rotor shaft; Blender axes nose +X,
left +Y, up +Z (the .glb: x nose, y up, z right).

Measured from the three-view (40.85 px/m; the drawing, a second dimensioned drawing and
photos were reference only and are not in the repository): nose tip X 4.64; wheels X 2.93
and -1.40 (4.34 m wheelbase), main track 3.04 m; tail rotor shaft X -8.40 (8.40 m between
the shafts, second drawing), 2.70 m across, on the right (helis.com: "starboard mounted");
stabiliser span 4.22 m at Z 1.9; main rotor 13.80 m across, five blades, plane Z 3.57, shaft
tilted 5 deg forward as drawn; overall length 16.66 m; tail rotor blades 0.44 m right of the
centre line, fin and gearbox fairing as drawn (side and top views); door outlines from the side
view. Cross sections, windows and gear legs are traced or estimated from the drawing and photos;
the two antennas (roof whip, blade under the boom) are placed from the photos.

Parts that turn carry glTF extras (custom properties, in the .glb's axes): spin_pivot,
spin_axis, spin_rev_per_s, spin_blades; the viewer spins them (realAirfields.js; the blade
count caps the turn per frame so a slow frame rate never makes them look backwards). Main rotor 296 rpm, its
speed at 100 % NR (AW139 B2 training notes, 2010: "the 296 rpm of the MR speed (at 100% Nr)";
EASA TCDS R.006 gives NR only in per cent), anticlockwise seen from above (Vertipedia:
"Counterclockwise"). Tail rotor 6 rev/s, much slower than the real 1435 rpm (same notes),
which would strobe at screen frame rates; it turns top blade forward, a direction not checked
against a source. Materials drawn a few millimetres off the skin (windows, grilles) carry
polygon_offset.
"""
import math

import bmesh
import bpy
import numpy as np

COLL = "AW139"
N_AROUND = 72

# ------------------------------------------------------------------ helpers

def pchip(xk, yk, x):
    xk = np.asarray(xk, float); yk = np.asarray(yk, float)
    o = np.argsort(xk); xk, yk = xk[o], yk[o]
    h = np.diff(xk); d = np.diff(yk) / h
    m = np.zeros_like(yk)
    for i in range(1, len(xk) - 1):
        if d[i - 1] * d[i] > 0:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])
    m[0], m[-1] = d[0], d[-1]
    if len(m) > 2:
        if m[0] * m[1] < 0: m[0] = 0
        if m[-1] * m[-2] < 0: m[-1] = 0
    x = np.asarray(x, float)
    i = np.clip(np.searchsorted(xk, x) - 1, 0, len(xk) - 2)
    t = (x - xk[i]) / h[i]
    return ((2 * t**3 - 3 * t**2 + 1) * yk[i] + (t**3 - 2 * t**2 + t) * h[i] * m[i]
            + (-2 * t**3 + 3 * t**2) * yk[i + 1] + (t**3 - t**2) * h[i] * m[i + 1])


def ring(yc, zb, zt, hw, zw, nt, nb, n=N_AROUND, M=1440):
    """Superellipse section (top and bottom halves with their own exponent and height),
    resampled to n points evenly along its perimeter, starting at the +Y side."""
    t = np.linspace(0, 2 * np.pi, M, endpoint=False)
    c, s = np.cos(t), np.sin(t)
    e = np.where(s >= 0, nt, nb)
    hz = np.where(s >= 0, zt - zw, zw - zb)
    pts = np.stack([yc + hw * np.sign(c) * np.abs(c) ** (2 / e), zw + hz * np.sign(s) * np.abs(s) ** (2 / e)], 1)
    closed = np.vstack([pts, pts[:1]])
    L = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(closed, axis=0), axis=1))])
    u = np.linspace(0, L[-1], n, endpoint=False)
    return np.stack([np.interp(u, L, closed[:, 0]), np.interp(u, L, closed[:, 1])], 1)


def make_object(name, verts, faces, mats, smooth=True):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in verts], [], faces)
    bm = bmesh.new(); bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-5)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(me); bm.free()
    for m in mats: me.materials.append(bpy.data.materials[m])
    if smooth: me.shade_smooth()
    ob = bpy.data.objects.new(name, me)
    bpy.data.collections[COLL].objects.link(ob)
    return ob


def grid_faces(n_rings, n, closed=True, start=0):
    f = []
    for a in range(n_rings - 1):
        for j in range(n if closed else n - 1):
            k = (j + 1) % n
            f.append((start + a * n + j, start + a * n + k, start + (a + 1) * n + k, start + (a + 1) * n + j))
    return f


def loft_x(name, table, X, mats, tip_front=None, tip_back=None, n=N_AROUND):
    """Loft along X: table rows (X, yc, zb, zt, hw, zw, nt, nb); X sorted front (high) to back."""
    tb = np.array(table, float)
    p = [pchip(tb[:, 0], tb[:, k], X) for k in range(1, 8)]
    verts = []
    for i, x in enumerate(X):
        verts += [(x, y, z) for y, z in ring(*[q[i] for q in p], n=n)]
    faces = grid_faces(len(X), n)
    if tip_front is not None:
        verts.append(tip_front); f = len(verts) - 1
        faces += [(f, (j + 1) % n, j) for j in range(n)]
    if tip_back is not None:
        verts.append(tip_back); f = len(verts) - 1; b = (len(X) - 1) * n
        faces += [(f, b + j, b + (j + 1) % n) for j in range(n)]
    return make_object(name, verts, faces, mats)


def airfoil(le, te, t, n=40):
    """Symmetric NACA 00xx outline around the chord from le to te (2-D points), closed loop."""
    b = np.linspace(0, np.pi, n // 2 + 1)
    xc = (1 - np.cos(b)) / 2
    yt = 5 * t * (0.2969 * np.sqrt(xc) - 0.126 * xc - 0.3516 * xc**2 + 0.2843 * xc**3 - 0.1036 * xc**4)
    le, te = np.asarray(le, float), np.asarray(te, float)
    ch = te - le; c = np.linalg.norm(ch); u = ch / c; v = np.array([-u[1], u[0]])
    up = [le + u * c * x + v * c * y for x, y in zip(xc, yt)]
    lo = [le + u * c * x - v * c * y for x, y in zip(xc[1:-1][::-1], yt[1:-1][::-1])]
    return np.array(up + lo)


def loft_sections(name, sections, mats, cap=True):
    """Sections: list of equal-length 3-D point loops."""
    n = len(sections[0])
    verts = [tuple(p) for s in sections for p in s]
    faces = grid_faces(len(sections), n)
    if cap:
        faces.append(tuple(range(n))[::-1])
        b = (len(sections) - 1) * n
        faces.append(tuple(range(b, b + n)))
    return make_object(name, verts, faces, mats)


def cylinder(name, p0, p1, r, mats, n=24, cap=True):
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    ax = p1 - p0; ax /= np.linalg.norm(ax)
    a = np.cross(ax, [0, 0, 1] if abs(ax[2]) < 0.9 else [1, 0, 0]); a /= np.linalg.norm(a)
    b = np.cross(ax, a)
    ang = np.linspace(0, 2 * np.pi, n, endpoint=False)
    loop = [np.cos(q) * a * r + np.sin(q) * b * r for q in ang]
    return loft_sections(name, [[p0 + d for d in loop], [p1 + d for d in loop]], mats, cap=cap)


def wheel(name, centre, r, width, axis=(0, 1, 0)):
    c = np.asarray(centre, float); ax = np.asarray(axis, float) * width / 2
    # tyre: rounded profile lofted around the axle
    prof = [(r * 0.62, -1.0), (r * 0.92, -1.0), (r, -0.6), (r, 0.6), (r * 0.92, 1.0), (r * 0.62, 1.0)]
    ang = np.linspace(0, 2 * np.pi, 32, endpoint=False)
    secs = []
    for rr, w in prof:
        secs.append([c + ax * w + np.array([math.cos(q) * rr, 0, math.sin(q) * rr]) for q in ang])
    t = loft_sections(name + "Tyre", secs, ["AW139_Rubber"])
    h = cylinder(name + "Hub", c - ax * 0.9, c + ax * 0.9, r * 0.62, ["AW139_Metal"], n=32)
    return t, h


def inside_poly(px, pz, poly):
    poly = np.asarray(poly); inside = np.zeros_like(px, bool)
    j = len(poly) - 1
    for i in range(len(poly)):
        xi, zi = poly[i]; xj, zj = poly[j]
        cross = ((zi > pz) != (zj > pz)) & (px < (xj - xi) * (pz - zi) / (zj - zi + 1e-12) + xi)
        inside ^= cross; j = i
    return inside


def rrect(px, pz, x0, x1, z0, z1, r):
    cx, cz = np.clip(px, min(x0, x1) + r, max(x0, x1) - r), np.clip(pz, z0 + r, z1 - r)
    return (np.hypot(px - cx, pz - cz) <= r) & (px >= min(x0, x1)) & (px <= max(x0, x1)) & (pz >= z0) & (pz <= z1)


def paint_faces(ob, rule):
    """rule(centres, normals) -> material index per face."""
    me = ob.data
    c = np.array([p.center for p in me.polygons]); nrm = np.array([p.normal for p in me.polygons])
    idx = rule(c, nrm)
    me.polygons.foreach_set("material_index", idx.astype(np.int32))
    me.update()

# ------------------------------------------------------------------ setup

def material(name, rgb, rough=0.4, metal=0.0):
    m = bpy.data.materials.get(name) or bpy.data.materials.new(name)
    m.use_nodes = True
    bsdf = m.node_tree.nodes.get("Principled BSDF")
    bsdf.inputs["Base Color"].default_value = (*rgb, 1)
    bsdf.inputs["Roughness"].default_value = rough
    bsdf.inputs["Metallic"].default_value = metal
    m.diffuse_color = (*rgb, 1)
    return m

material("AW139_Paint", (0.92, 0.92, 0.91), 0.35)
material("AW139_Glass", (0.03, 0.04, 0.05), 0.08)["polygon_offset"] = True  # windows: 8 mm off the skin
material("AW139_Grille", (0.05, 0.05, 0.05), 0.7)["polygon_offset"] = True
material("AW139_Blade", (0.18, 0.19, 0.2), 0.5)
material("AW139_Metal", (0.55, 0.56, 0.58), 0.35, 0.8)
material("AW139_Rubber", (0.03, 0.03, 0.03), 0.9)
material("AW139_Exhaust", (0.12, 0.11, 0.1), 0.6, 0.5)
material("AW139_Seam", (0.42, 0.43, 0.44), 0.5)["polygon_offset"] = True  # door outlines: 8 mm off the skin
material("AW139_Antenna", (0.25, 0.26, 0.27), 0.5)

col = bpy.data.collections.get(COLL)
if col is None:
    col = bpy.data.collections.new(COLL); bpy.context.scene.collection.children.link(col)
for ob in list(col.objects):
    bpy.data.objects.remove(ob, do_unlink=True)
for me in [m for m in bpy.data.meshes if m.users == 0]:
    bpy.data.meshes.remove(me)

# ------------------------------------------------------------------ fuselage

#        X     yc   zb    zt    hw    zw    nt   nb
FUS = [(4.62, 0, 0.80, 0.92, 0.05, 0.86, 2.2, 2.2),
       (4.55, 0, 0.74, 0.99, 0.15, 0.86, 2.2, 2.2),
       (4.35, 0, 0.66, 1.09, 0.31, 0.85, 2.3, 2.3),
       (4.00, 0, 0.59, 1.24, 0.49, 0.86, 2.5, 2.5),
       (3.60, 0, 0.53, 1.37, 0.67, 0.90, 2.8, 3.0),
       (3.40, 0, 0.50, 1.46, 0.76, 0.93, 3.0, 3.5),
       (3.00, 0, 0.46, 1.81, 0.91, 1.00, 3.0, 4.0),
       (2.60, 0, 0.45, 2.15, 1.01, 1.10, 3.2, 5.0),
       (2.20, 0, 0.44, 2.27, 1.07, 1.20, 3.5, 5.0),
       (1.60, 0, 0.44, 2.30, 1.10, 1.25, 3.5, 5.5),
       (-1.80, 0, 0.44, 2.30, 1.10, 1.25, 3.5, 5.5),
       (-2.20, 0, 0.52, 2.33, 1.06, 1.30, 3.3, 5.0),
       (-2.80, 0, 0.80, 2.41, 0.88, 1.45, 3.0, 4.0),
       (-3.40, 0, 1.07, 2.50, 0.62, 1.70, 2.8, 3.0),
       (-3.75, 0, 1.22, 2.56, 0.46, 1.85, 2.6, 2.6),
       (-4.60, 0, 1.35, 2.55, 0.36, 1.95, 2.4, 2.4),
       (-6.00, 0, 1.56, 2.53, 0.25, 2.05, 2.6, 2.3),
       (-7.05, 0, 1.71, 2.51, 0.18, 2.10, 3.2, 2.2),
       (-7.80, 0, 1.90, 2.50, 0.14, 2.20, 3.2, 2.2),
       (-8.30, 0, 2.08, 2.48, 0.09, 2.28, 3.2, 2.2)]
X = np.concatenate([np.arange(4.62, 2.2, -0.05), np.arange(2.2, -2.2, -0.1), np.arange(-2.2, -8.30, -0.15), [-8.30]])
fus = loft_x("AW139_Fuselage", FUS, X, ["AW139_Paint"],
             tip_front=(4.665, 0, 0.86), tip_back=(-8.36, 0, 2.28))

# up to the centre post; its top edge level with the roof panel (Z 2.03 in the front view)
WINDSCREEN = [(3.60, 1.58), (3.42, 1.44), (3.02, 1.12), (2.60, 1.15), (2.44, 2.03), (3.60, 2.03)]
SIDE_WINDOWS = [  # (x0, x1, z0, z1, corner radius) on both sides; the cockpit door's is a polygon
    (1.19, 1.68, 1.22, 1.92, 0.10), (0.23, 0.88, 1.22, 1.92, 0.12), (-0.56, 0.09, 1.22, 1.92, 0.12)]
DOOR_WINDOW = [(2.45, 1.96), (2.00, 1.96), (1.96, 1.18), (2.58, 1.18)]
# the roof windows over the pilots, seen from above (top view: X 1.93-2.62, 0.25-0.98 m off the
# centre line; photos: tinted, on the roof's shoulder above the cockpit doors): (x0, x1, y0, y1,
# corner radius) of |Y|, kept above the door frame (Z 2.06)
ROOF_WINDOW = (1.93, 2.62, 0.25, 0.98, 0.08)
# the bubble windows below the pilots' feet: the side view's extent (X 2.96 to 3.67, Z 0.51 to
# 0.96), shaped as both photos show them, a D: the rear edge upright, the bottom along the nose's
# underside, the top a convex arc from a rounded top rear corner down to a blunt front tip;
# bulged 35 mm off the skin (photos)
def _chin(n=24, e=2.3):
    zb = lambda x: 0.51 + (x - 2.96) * 0.06 / 0.71  # the nose's underside rises forward
    arc = [(2.96 + 0.71 * math.cos(t) ** (2 / e), zb(2.96 + 0.71 * math.cos(t) ** (2 / e)) + (0.96 - zb(2.96 + 0.71 * math.cos(t) ** (2 / e))) * math.sin(t) ** (2 / e))
           for t in np.linspace(0.06, math.pi / 2 - 0.02, n)]
    return [(2.97, 0.51)] + arc + [(2.96, 0.93), (2.96, 0.55)]
CHIN_WINDOW = _chin()
FUS_P = np.array(FUS, float)


def window_patch(name, inside, x0, x1, sgn, dx=0.025, k=10, lift=0.008, table=None, mat="AW139_Glass", bulge=0.0):
    """A glass panel lying on the fuselage: at each station the arc of the section where
    inside(x, y, z) holds on the sgn side, with its ends found to a few millimetres. bulge:
    how far its middle stands off the skin (a bubble window; zero at its edges)."""
    rows = []
    for x in np.arange(x1, x0, -dx):
        tb = FUS_P if table is None else table
        prm = [float(pchip(tb[:, 0], tb[:, c], x)) for c in range(1, 8)]
        r = ring(*prm, n=1600)
        ok = inside(np.full(len(r), x), r[:, 0], r[:, 1]) & (sgn * r[:, 0] > 0)
        if ok.sum() < 2:
            rows.append(None); continue
        # the longest circular run of True
        idx = np.nonzero(ok)[0]; best, cur = [idx[0], idx[0]], [idx[0], idx[0]]
        for a, b in zip(idx, idx[1:]):
            cur = [cur[0], b] if b == a + 1 else [b, b]
            if cur[1] - cur[0] > best[1] - best[0]: best = list(cur)
        seg = r[best[0]:best[1] + 1]
        L = np.concatenate([[0], np.cumsum(np.linalg.norm(np.diff(seg, axis=0), axis=1))])
        u = np.linspace(0, L[-1], k)
        pts = np.stack([np.interp(u, L, seg[:, 0]), np.interp(u, L, seg[:, 1])], 1)
        t = np.gradient(pts, axis=0); nrm = np.stack([t[:, 1], -t[:, 0]], 1)
        nrm /= np.linalg.norm(nrm, axis=1, keepdims=True) + 1e-12
        mid = pts[k // 2] - np.array([0, prm[4]])  # outward = away from the section centre
        if np.dot(nrm[k // 2], mid) < 0: nrm = -nrm
        rows.append((x, pts + nrm * lift, nrm))
    if bulge:
        idx = [i for i, r in enumerate(rows) if r is not None]
        m = len(idx); arc = np.sin(np.pi * np.arange(k) / (k - 1))
        for n_, i in enumerate(idx):
            x, pts, nrm = rows[i]
            rows[i] = (x, pts + nrm * (bulge * math.sin(math.pi * n_ / max(1, m - 1)) * arc)[:, None], nrm)
    rows = [None if r is None else [(r[0], y, z) for y, z in r[1]] for r in rows]
    verts, faces = [], []
    prev = None
    for row in rows:
        if row is None:
            prev = None; continue
        base = len(verts); verts += row
        if prev is not None:
            faces += [(prev + j, prev + j + 1, base + j + 1, base + j) for j in range(k - 1)]
        prev = base
    return make_object(name, verts, faces, [mat])


for s, sgn in (("L", 1), ("R", -1)):
    window_patch(f"AW139_Windscreen{s}", lambda x, y, z: inside_poly(x, z, WINDSCREEN) & (np.abs(y) > 0.04), 2.40, 3.45, sgn, dx=0.02, k=14)
    window_patch(f"AW139_DoorWindow{s}", lambda x, y, z: inside_poly(x, z, DOOR_WINDOW), 1.94, 2.60, sgn)
    window_patch(f"AW139_RoofWindow{s}", lambda x, y, z: rrect(x, np.abs(y), *ROOF_WINDOW) & (z > 2.06), 1.92, 2.63, sgn, k=14)
    for i, w in enumerate(SIDE_WINDOWS):
        window_patch(f"AW139_CabinWindow{s}{i}", lambda x, y, z, w=w: rrect(x, z, *w), w[0] - 0.01, w[1] + 0.01, sgn)
    window_patch(f"AW139_ChinWindow{s}", lambda x, y, z: inside_poly(x, z, CHIN_WINDOW), 2.94, 3.69, sgn, dx=0.015, k=14, bulge=0.035)


# door outlines (the side view, (X, Z) on both sides): the cockpit door behind the windscreen
# post, the sliding cabin door round the two windows (its lower rear corner cut), the baggage
# door aft of the cabin; drawn as 25 mm grey seams on the skin
COCKPIT_DOOR = [(2.47, 2.02), (1.88, 2.02), (1.88, 0.70), (2.76, 0.70), (2.76, 0.82), (2.62, 1.15)]
CABIN_DOOR = [(1.03, 2.08), (-0.67, 2.08), (-0.67, 0.96), (-0.40, 0.70), (1.03, 0.70)]
BAGGAGE_DOOR = (-3.16, -2.20, 1.19, 1.90, 0.06)  # x0, x1, z0, z1, corner radius


def skin_point(x, z, sgn, lift=0.008):
    """The fuselage skin on the sgn side at station x and height z, lifted along its normal."""
    prm = [float(pchip(FUS_P[:, 0], FUS_P[:, c], x)) for c in range(1, 8)]
    r = ring(*prm, n=720)
    side = r[sgn * r[:, 0] > 0]
    o = np.argsort(side[:, 1]); zz, yy = side[o, 1], side[o, 0]
    y = np.interp(z, zz, yy); dy = np.interp(z + 0.01, zz, yy) - np.interp(z - 0.01, zz, yy)
    n = np.array([0.02, -dy]) * sgn; n /= np.linalg.norm(n)
    return (x, y + n[0] * lift, z + n[1] * lift)


def rrect_path(x0, x1, z0, z1, r, k=6):
    pts = []
    for cx, cz, a0 in ((x1 - r, z1 - r, 0), (x0 + r, z1 - r, 90), (x0 + r, z0 + r, 180), (x1 - r, z0 + r, 270)):
        pts += [(cx + r * math.cos(math.radians(a0 + 90 * i / k)), cz + r * math.sin(math.radians(a0 + 90 * i / k))) for i in range(k + 1)]
    return pts


def seam(name, path, sgn, w=0.025, step=0.02):
    """A closed outline (X, Z) drawn on the skin as a ribbon w wide."""
    path = np.asarray(path, float); closed = np.vstack([path, path[:1]])
    pts = []
    for a, b in zip(closed, closed[1:]):
        m = max(1, int(np.linalg.norm(b - a) / step))
        pts += [a + (b - a) * i / m for i in range(m)]
    pts = np.array(pts); n = len(pts)
    t = np.roll(pts, -1, 0) - np.roll(pts, 1, 0); t /= np.linalg.norm(t, axis=1, keepdims=True)
    perp = np.stack([-t[:, 1], t[:, 0]], 1) * w / 2
    verts = [skin_point(*q, sgn) for q in pts + perp] + [skin_point(*q, sgn) for q in pts - perp]
    faces = [(j, (j + 1) % n, n + (j + 1) % n, n + j) for j in range(n)]
    return make_object(name, verts, faces, ["AW139_Seam"], smooth=False)


for s, sgn in (("L", 1), ("R", -1)):
    seam(f"AW139_CockpitDoor{s}", COCKPIT_DOOR, sgn)
    seam(f"AW139_CabinDoor{s}", CABIN_DOOR, sgn)
    seam(f"AW139_BaggageDoor{s}", rrect_path(*BAGGAGE_DOOR), sgn)


# engine cowling over the cabin roof, and the fairing round the rotor shaft
COWL = [(2.40, 0, 2.00, 2.28, 0.30, 2.10, 2.5, 2.0),
        (2.00, 0, 2.00, 2.45, 0.52, 2.15, 2.6, 2.0),
        (1.50, 0, 2.00, 2.66, 0.71, 2.20, 3.0, 2.0),
        (1.00, 0, 2.00, 2.88, 0.82, 2.25, 3.0, 2.0),
        (0.50, 0, 2.00, 2.96, 0.86, 2.30, 3.2, 2.0),
        (-1.70, 0, 2.00, 3.00, 0.85, 2.30, 3.2, 2.0),
        (-2.80, 0, 2.00, 2.82, 0.66, 2.30, 3.0, 2.0),
        (-3.75, 0, 2.00, 2.58, 0.44, 2.20, 2.6, 2.0),
        (-4.40, 0, 2.00, 2.53, 0.33, 2.10, 2.4, 2.0)]
cowl = loft_x("AW139_Cowling", COWL, np.arange(2.40, -4.41, -0.15), ["AW139_Paint"],
              tip_front=(2.45, 0, 2.10), tip_back=(-4.45, 0, 2.10))
# the engine air intakes: a screen on each side of the cowling behind the mast. The side view
# hatches X -1.46 to -1.84, Z 2.38 to 2.95; both photos show the screen reaching forward to about
# X -1.05 (measured between the mast and the exhausts), with rounded top corners (one of them
# has an inlet filter box over it, not modelled)
for s, sgn in (("L", 1), ("R", -1)):
    window_patch(f"AW139_Intake{s}", lambda x, y, z: rrect(x, z, -1.84, -1.05, 2.38, 2.95, 0.10), -1.86, -1.03, sgn,
                 dx=0.02, table=np.array(COWL, float), mat="AW139_Grille")

MAST_FAIRING = [(1.20, 0, 2.60, 2.86, 0.30, 2.70, 2.5, 2.0),
                (0.60, 0, 2.60, 3.20, 0.48, 2.75, 2.8, 2.0),
                (-0.60, 0, 2.60, 3.22, 0.50, 2.75, 2.8, 2.0),
                (-1.30, 0, 2.60, 3.06, 0.44, 2.75, 2.6, 2.0),
                (-1.80, 0, 2.60, 2.95, 0.30, 2.75, 2.4, 2.0)]
loft_x("AW139_MastFairing", MAST_FAIRING, np.arange(1.20, -1.81, -0.12), ["AW139_Paint"],
       tip_front=(1.25, 0, 2.72), tip_back=(-1.85, 0, 2.72), n=48)

# sponsons (house the retracted main gear): rows X, y_in, y_out, zb, zt
for s, sgn in (("L", 1), ("R", -1)):
    SP = [(1.05, 0.95, 1.00, 0.44, 0.60), (0.80, 0.95, 1.20, 0.44, 0.66), (0.30, 0.95, 1.40, 0.44, 0.70),
          (-0.40, 0.95, 1.55, 0.44, 0.72), (-1.90, 0.95, 1.57, 0.44, 0.72), (-2.10, 0.95, 1.48, 0.46, 0.70)]
    tb = [(x, sgn * (a + b) / 2, zb, zt, (b - a) / 2, (zb + zt) / 2, 3.0, 4.0) for x, a, b, zb, zt in SP]
    loft_x(f"AW139_Sponson_{s}", tb, np.arange(1.05, -2.11, -0.12), ["AW139_Paint"],
           tip_front=(1.08, sgn * 0.975, 0.52), tip_back=(-2.20, sgn * 1.22, 0.58), n=32)

# ------------------------------------------------------------------ tail

def fin_section(z, le, te, t, y=0.0):
    return [(p[0], y + p[1], z) for p in airfoil((le, 0), (te, 0), t)]

# the fin (side view): a straight swept leading edge from a fillet on the boom top (X -6.55 at
# Z 2.49, X -7.03 at Z 2.63) to the top front corner (X -8.23, Z 3.78); the trailing edge from
# the top rear corner (X -9.03, Z 3.64) down to Z 2.51, then the S of the tail bumper (X -8.11
# at Z 2.33, out to X -8.32 at Z 2.11) to the bottom at Z 2.02. As thick as the boom where it
# grows out of it (about 0.25 m; the boom's top is squarer there); below Z 2.5 forward of X -8
# it is inside the boom.
FIN_Z = [2.02, 2.11, 2.20, 2.33, 2.45, 2.49, 2.53, 2.58, 2.63, 3.00, 3.40, 3.64, 3.74, 3.80]
FIN_LE = [-7.30, -7.10, -6.90, -6.70, -6.56, -6.55, -6.82, -6.95, -7.03, -7.41, -7.83, -8.08, -8.20, -8.30]
FIN_TE = [-8.32, -8.32, -8.16, -8.11, -8.17, -8.19, -8.21, -8.25, -8.29, -8.56, -8.85, -9.03, -9.00, -8.90]
FIN_T = [0.14, 0.14, 0.15, 0.15, 0.15, 0.15, 0.17, 0.20, 0.22, 0.24, 0.24, 0.24, 0.22, 0.12]
fz = np.linspace(FIN_Z[0], FIN_Z[-1], 32)
loft_sections("AW139_Fin", [fin_section(z, le, te, t) for z, le, te, t in
                            zip(fz, pchip(FIN_Z, FIN_LE, fz), pchip(FIN_Z, FIN_TE, fz), pchip(FIN_Z, FIN_T, fz))], ["AW139_Paint"])

# the tail rotor gearbox fairing on the fin top: a teardrop 0.7 m across (top view: X -8.15 to
# the round end at X -9.06), the tail rotor on its right
loft_x("AW139_TailFairing", [(-8.12, 0, 3.58, 3.76, 0.12, 3.68, 2.2, 2.2), (-8.25, 0, 3.50, 3.80, 0.26, 3.66, 2.4, 2.4),
                             (-8.45, 0, 3.44, 3.81, 0.34, 3.64, 2.6, 2.6), (-8.70, 0, 3.44, 3.79, 0.34, 3.63, 2.6, 2.6),
                             (-8.90, 0, 3.48, 3.74, 0.26, 3.63, 2.4, 2.4), (-9.02, 0, 3.55, 3.69, 0.14, 3.63, 2.2, 2.2)],
       np.linspace(-8.12, -9.02, 16), ["AW139_Paint"], tip_front=(-8.06, 0, 3.69), tip_back=(-9.08, 0, 3.64), n=40)

STAB_Z, STAB_LE, STAB_TE, STAB_HALF = 1.90, -7.05, -7.67, 2.05
secs = []
for yy in np.concatenate([np.linspace(-STAB_HALF, -STAB_HALF + 0.15, 4), np.linspace(-1.8, 1.8, 7), np.linspace(STAB_HALF - 0.15, STAB_HALF, 4)]):
    k = math.sqrt(max(0.0, 1 - max(0.0, (abs(yy) - (STAB_HALF - 0.15)) / 0.15) ** 2))  # rounded tips
    ch = (STAB_LE - STAB_TE) * (0.35 + 0.65 * k)
    le = STAB_LE - (STAB_LE - STAB_TE - ch) * 0.3
    secs.append([(p[0], yy, STAB_Z + p[1]) for p in airfoil((le, 0), (le - ch, 0), 0.12)])
loft_sections("AW139_Stabilizer", secs, ["AW139_Paint"])
for s, sgn in (("L", 1), ("R", -1)):  # end plates, turned up and out
    ep = [[(p[0], sgn * (STAB_HALF - 0.02 + 0.25 * (z - 1.86)), z) for p in airfoil((le, 0), (le - c, 0), 0.10)]
          for z, le, c in ((1.86, -7.08, 0.52), (2.02, -7.16, 0.42), (2.16, -7.26, 0.30))]
    loft_sections(f"AW139_EndPlate_{s}", ep, ["AW139_Paint"])

# tail rotor on the right (starboard) side of the fin, gearbox fairing at the fin top
TR = np.array([-8.40, -0.36, 3.70])  # blades 0.44 m right of the centre line (top view)
fair = cylinder("AW139_TailGearbox", TR + (0, 0.30, 0), TR + (0, -0.02, 0), 0.14, ["AW139_Paint"], n=24)
cylinder("AW139_TailHub", TR + (0, 0.0, 0), TR + (0, -0.12, 0), 0.08, ["AW139_Metal"], n=16)
for i in range(4):
    a = math.radians(90 * i + 20)
    d = np.array([math.cos(a), 0, math.sin(a)]); u = np.array([-math.sin(a), 0, math.cos(a)])
    sec = []
    for r, c in ((0.12, 0.20), (1.30, 0.19), (1.35, 0.12)):
        sec.append([TR + (0, -0.08, 0) + d * r + u * p[0] + np.array([0, p[1], 0]) for p in airfoil((0.08, 0), (-0.08 - (c - 0.16), 0), 0.10, n=16)])
    loft_sections(f"AW139_TailBlade{i}", sec, ["AW139_Blade"])

# engine exhausts: round pipes pointing aft and outboard from the cowling sides
for s, sgn in (("L", 1), ("R", -1)):
    p0 = np.array([-2.85, sgn * 0.50, 2.58]); d = np.array([-math.cos(math.radians(35)), sgn * math.sin(math.radians(35)), 0.12])
    cylinder(f"AW139_Exhaust_{s}", p0, p0 + d * 0.42, 0.17, ["AW139_Paint"], n=24, cap=False)
    cylinder(f"AW139_ExhaustPipe_{s}", p0 + d * 0.05, p0 + d * 0.40, 0.13, ["AW139_Exhaust"], n=24)

# antennas seen in the photos: a whip on the left roof edge behind the cockpit (beside the
# engine cowling), raked aft, and a blade under the front of the tail boom
cylinder("AW139_AntennaWhip", (1.65, 0.85, 2.13), (1.65 - 0.6 * math.sin(math.radians(30)), 0.85, 2.13 + 0.6 * math.cos(math.radians(30))),
         0.008, ["AW139_Antenna"], n=8)
loft_sections("AW139_AntennaBlade", [[(p[0], p[1], z) for p in airfoil((le, 0), (le - c, 0), 0.12, n=16)]
                                     for z, le, c in ((1.28, -3.68, 0.26), (0.98, -3.80, 0.16))], ["AW139_Antenna"])

# ------------------------------------------------------------------ gear

for s, sgn in (("L", 1), ("R", -1)):
    wheel(f"AW139_NoseWheel{s}", (2.93, sgn * 0.17, 0.19), 0.19, 0.12)
    wheel(f"AW139_MainWheel{s}", (-1.40, sgn * 1.52, 0.22), 0.22, 0.18)
    cylinder(f"AW139_MainLeg{s}", (-1.05, sgn * 1.42, 0.55), (-1.40, sgn * 1.42, 0.22), 0.05, ["AW139_Metal"], n=12)
    cylinder(f"AW139_MainAxle{s}", (-1.40, sgn * 1.40, 0.22), (-1.40, sgn * 1.52, 0.22), 0.04, ["AW139_Metal"], n=12)
cylinder("AW139_NoseLeg", (2.86, 0, 0.50), (2.93, 0, 0.19), 0.055, ["AW139_Metal"], n=12)
cylinder("AW139_NoseAxle", (2.93, -0.17, 0.19), (2.93, 0.17, 0.19), 0.035, ["AW139_Metal"], n=12)

# ------------------------------------------------------------------ main rotor (shaft tilted 5 deg forward, as drawn)

rotor = []
rotor.append(cylinder("AW139_Mast", (0, 0, 3.10), (0, 0, 3.58), 0.12, ["AW139_Metal"], n=20))
rotor.append(cylinder("AW139_HubPlate", (0, 0, 3.50), (0, 0, 3.62), 0.42, ["AW139_Metal"], n=40))
cap = []
for zz, rr in ((3.62, 0.44), (3.74, 0.43), (3.84, 0.37), (3.90, 0.26), (3.93, 0.10)):
    cap.append([(rr * math.cos(q), rr * math.sin(q), zz) for q in np.linspace(0, 2 * np.pi, 40, endpoint=False)])
rotor.append(loft_sections("AW139_HubCap", cap, ["AW139_Paint"]))
R, CHORD = 6.90, 0.52
for i in range(5):
    a = math.radians(72 * i + 18)
    d = np.array([math.cos(a), math.sin(a), 0]); u = np.array([-math.sin(a), math.cos(a), 0])  # u: leading edge (rotor turns anticlockwise from above)
    sec = []
    for r, c, back in ((0.42, 0.20, 0.0), (0.9, 0.30, 0.0), (1.6, CHORD, 0.0), (6.20, CHORD, 0.0), (6.60, 0.42, 0.10), (6.90, 0.20, 0.30)):
        droop = -0.10 * (max(0.0, r - 0.9) / (R - 0.9)) ** 1.5
        base = np.array([0, 0, 3.57 + droop]) + d * r - u * back
        sec.append([base + u * (c * 0.25 - p[0]) + np.array([0, 0, p[1]]) for p in airfoil((0, 0), (c, 0), 0.10 if r > 1 else 0.25, n=24)])
    rotor.append(loft_sections(f"AW139_MainBlade{i}", sec, ["AW139_Blade"]))
pivot = np.array([0, 0, 3.10]); tilt = math.radians(5)
for ob in rotor:
    for v in ob.data.vertices:
        x, y, z = np.array(v.co) - pivot
        v.co = (x * math.cos(tilt) + z * math.sin(tilt) + pivot[0], y + pivot[1], -x * math.sin(tilt) + z * math.cos(tilt) + pivot[2])
    ob.data.update()

# ------------------------------------------------------------------ turning parts (in the .glb's axes: x, z, -y)

def spin(objs, pivot_blender, axis_blender, rev_per_s, blades):
    px, py, pz = map(float, pivot_blender); ax, ay, az = map(float, axis_blender)
    for ob in objs:
        ob["spin_pivot"] = [px, pz, -py]
        ob["spin_axis"] = [ax, az, -ay]
        ob["spin_rev_per_s"] = rev_per_s
        ob["spin_blades"] = blades

spin(rotor, (0.0, 0.0, 3.10), (math.sin(tilt), 0.0, math.cos(tilt)), 296 / 60, 5)  # 100 % NR
spin([o for o in col.objects if o.name.startswith(("AW139_TailBlade", "AW139_TailHub"))], tuple(TR), (0.0, 1.0, 0.0), 6.0, 4)

if bpy.app.background:  # blender --background --python this file: write the viewer's model
    import pathlib
    path = pathlib.Path(__file__).resolve().parents[2] / "flightsim" / "viewer" / "models" / "aw139.glb"
    bpy.ops.object.select_all(action="DESELECT")
    for o in col.objects: o.select_set(True)
    bpy.context.view_layer.objects.active = col.objects[0]
    bpy.ops.export_scene.gltf(filepath=str(path), export_format="GLB", use_selection=True, export_apply=True,
                              export_yup=True, export_extras=True)
    print("written", path)

result = {"objects": len(col.objects), "triangles": sum(sum(len(p.vertices) - 2 for p in o.data.polygons) for o in col.objects)}
