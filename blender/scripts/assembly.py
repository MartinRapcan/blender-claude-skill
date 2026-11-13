"""
Assembly by attachment + contact audit (Blender 5.x, tested on 5.0.1 / 5.2.3).
See references/assembly.md.

The problem this solves: scenes built "layer after layer" with hand-typed
coordinates end up with windows that float in front of walls, signs that hang
in the air and parts that miss each other, because no layer looks at where the
previous one actually is.

The fix has two halves:

1. Build by ATTACHMENT. Never type world coordinates for a child part. Take a
   Frame (a rectangle in space: a wall, the side of a block, an opening) from
   the parent, and place the child in the frame's own coordinates:
   s along it, t up it, d out of it. Every builder function returns the frames
   of what it built, so the next layer starts from real geometry.

       side   = Frame.of_object(hull, "-Y")                         # the parent's own surface
       cells  = side.inset(0.2).grid(cols=3, rows=1, gap=0.3)       # regular layout, derived
       for cell in cells:
           panel = block(metal, cell, out=0.05)                     # sunk 2 cm into the hull
           block(lights, panel.front.inset(0.05), out=0.01)         # attached to the panel
       rod(pipes, side, 0.0, 0.3, side.w, 0.3, 0.03)                # a pipe ON that surface

2. AUDIT before rendering. audit() builds the contact graph of every part in
   the scene and reports parts or groups that touch nothing (floating), and
   coplanar overlapping faces (z-fighting), with the gap and direction to the
   nearest part so the offset can be fixed instead of guessed.

       report = audit(allow=("Rain*",))      # prints a report
       assert report["ok"], "fix detached parts before rendering"

Works with any builder that has .box(center, size, basis) and a .bm bmesh
(detail_kit.MeshBuilder). This module is subject-agnostic; walls with
openings, windows and facades live in architecture.py.
"""

import fnmatch
import json
import math

import bpy  # before bmesh / mathutils with the pip `bpy` module
import bmesh  # noqa: F401  (builders pass their bmesh in)
import numpy as np
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

EMBED = 0.02      # how far an attached part sinks into its parent (metres)
_TOL = 1e-4       # layout tolerance for "fits inside"

_SIDES = {  # side -> (u, v) so that u x v is the outward normal
    "+X": ((0, 1, 0), (0, 0, 1)), "-X": ((0, -1, 0), (0, 0, 1)),
    "+Y": ((-1, 0, 0), (0, 0, 1)), "-Y": ((1, 0, 0), (0, 0, 1)),
    "+Z": ((1, 0, 0), (0, 1, 0)), "-Z": ((1, 0, 0), (0, -1, 0)),
}


# ===================================================================== frames
class Frame:
    """A rectangle in space with its own coordinates.

    origin = lower-left corner, u = along (width), v = up (height),
    n = u x v = outward normal. at(s, t, d) converts frame coordinates
    (metres along, up, out) to a world position. All layout methods return new
    Frames, so positions are always derived, never retyped."""

    def __init__(self, origin, u, v, width, height, name=""):
        self.origin = Vector(origin)
        self.u, self.v = Vector(u).normalized(), Vector(v).normalized()
        if abs(self.u.dot(self.v)) > 1e-5:
            raise ValueError(f"Frame {name!r}: u and v must be perpendicular")
        if width <= 0 or height <= 0:
            raise ValueError(f"Frame {name!r}: size must be positive, got {width:.4f} x {height:.4f}")
        self.n = self.u.cross(self.v).normalized()
        self.w, self.h, self.name = float(width), float(height), name

    # --- constructors
    @classmethod
    def wall(cls, a, b, z0, height, name=""):
        """Vertical wall from 2D point a to b (walk CCW around a footprint so
        the normal points outside), from z0 up `height`."""
        a, b = Vector((a[0], a[1], z0)), Vector((b[0], b[1], z0))
        return cls(a, b - a, (0, 0, 1), (b - a).length, height, name)

    @classmethod
    def of_box(cls, center, size, side, name=""):
        """Side '+X', '-X', '+Y', '-Y', '+Z' or '-Z' of an axis-aligned box."""
        c, half = Vector(center), Vector(size) / 2
        u, v = (Vector(a) for a in _SIDES[side])
        n = u.cross(v)
        ext = lambda axis: abs(axis.x) * half.x + abs(axis.y) * half.y + abs(axis.z) * half.z  # noqa: E731
        origin = c + n * ext(n) - u * ext(u) - v * ext(v)
        return cls(origin, u, v, 2 * ext(u), 2 * ext(v), name or side)

    @classmethod
    def of_object(cls, obj, side, name=""):
        """Side of an object's world-space bounding box (after modifiers)."""
        dg = bpy.context.evaluated_depsgraph_get()
        ev = obj.evaluated_get(dg)
        pts = [ev.matrix_world @ Vector(c) for c in ev.bound_box]
        lo = Vector(tuple(min(p[i] for p in pts) for i in range(3)))
        hi = Vector(tuple(max(p[i] for p in pts) for i in range(3)))
        return cls.of_box((lo + hi) / 2, hi - lo, side, name or f"{obj.name} {side}")

    # --- short aliases
    L = property(lambda self: self.w)
    H = property(lambda self: self.h)
    p0 = property(lambda self: self.origin)

    @property
    def basis(self):
        """3x3 rotation: local x = u, y = n (outward), z = v. For builder.box()."""
        return Matrix((self.u, self.n, self.v)).transposed()

    @property
    def center(self):
        return self.at(self.w / 2, self.h / 2)

    def at(self, s, t, d=0.0):
        """World position of (s along, t up, d out of the surface)."""
        return self.origin + self.u * s + self.v * t + self.n * d

    def locate(self, point):
        """Inverse of at(): frame coordinates (s, t, d) of a world point."""
        r = Vector(point) - self.origin
        return r.dot(self.u), r.dot(self.v), r.dot(self.n)

    # --- layout
    def sub(self, s, t, w, h, name=""):
        """Sub-rectangle with lower-left corner (s, t). Raises if it sticks out."""
        if s < -_TOL or t < -_TOL or s + w > self.w + _TOL or t + h > self.h + _TOL:
            raise ValueError(
                f"{name or 'sub-frame'} (s {s:.3f}..{s + w:.3f}, t {t:.3f}..{t + h:.3f}) does not fit "
                f"inside frame {self.name!r} ({self.w:.3f} x {self.h:.3f}). Derive its position from "
                f"the parent (grid/columns/rows/centered) instead of typing numbers.")
        return Frame(self.at(s, t), self.u, self.v, w, h, name or self.name)

    def centered(self, w, h, s=None, t=None, name=""):
        """Sub-rectangle of size w x h centred at (s, t); default: frame centre."""
        s = self.w / 2 if s is None else s
        t = self.h / 2 if t is None else t
        return self.sub(s - w / 2, t - h / 2, w, h, name)

    def inset(self, left, right=None, bottom=None, top=None, name=""):
        """Shrink by margins. inset(m) = all sides; inset(side, None, bottom, top)."""
        right = left if right is None else right
        bottom = left if bottom is None else bottom
        top = bottom if top is None else top
        return self.sub(left, bottom, self.w - left - right, self.h - bottom - top, name)

    def offset(self, d, name=""):
        """Same rectangle moved d metres along the normal (negative = inward)."""
        return Frame(self.origin + self.n * d, self.u, self.v, self.w, self.h, name or self.name)

    def rel(self, ds, dt, w=None, h=None, name=""):
        """A neighbour in the same plane: lower-left corner at (ds, dt) from
        this frame's corner (may lie outside it), size w x h (default: same).
        For things placed relative to an opening: under it, above it, beside it."""
        return Frame(self.at(ds, dt), self.u, self.v, self.w if w is None else w,
                     self.h if h is None else h, name or self.name)

    def flipped(self, name=""):
        """Same rectangle seen from the other side (normal reversed)."""
        return Frame(self.at(self.w, 0), -self.u, self.v, self.w, self.h, name or self.name)

    @staticmethod
    def _resolve(total, spec, gap, margin, what):
        if isinstance(spec, int):
            spec = [None] * spec
        n = len(spec)
        free = total - 2 * margin - gap * (n - 1) - sum(x for x in spec if x is not None)
        flex = sum(1 for x in spec if x is None)
        if free < -_TOL or (flex and free <= 0):
            raise ValueError(f"{what} need {total - free:.3f} m but only {total:.3f} m are available")
        share = free / flex if flex else 0.0
        sizes = [share if x is None else x for x in spec]
        if not flex:
            margin += free / 2      # centre a fixed-size run
        starts, pos = [], margin
        for size in sizes:
            starts.append(pos)
            pos += size + gap
        return list(zip(starts, sizes))

    def columns(self, spec, gap=0.0, margin=0.0):
        """Split along u. `spec` = count (equal columns) or a list of widths
        where None means "share what is left". Returns Frames left to right."""
        return [self.sub(s, 0, w, self.h, f"{self.name} col{i}")
                for i, (s, w) in enumerate(self._resolve(self.w, spec, gap, margin, "columns"))]

    def rows(self, spec, gap=0.0, margin=0.0):
        """Split along v, bottom to top. Same spec as columns()."""
        return [self.sub(0, t, self.w, h, f"{self.name} row{j}")
                for j, (t, h) in enumerate(self._resolve(self.h, spec, gap, margin, "rows"))]

    def grid(self, cols, rows, gap=0.0, margin=0.0):
        """Regular grid of cells, row by row from the bottom. Each cell has
        .col and .row. gap/margin may be a number or (horizontal, vertical)."""
        gu, gv = gap if isinstance(gap, (tuple, list)) else (gap, gap)
        mu, mv = margin if isinstance(margin, (tuple, list)) else (margin, margin)
        out = []
        for j, (t, h) in enumerate(self._resolve(self.h, rows, gv, mv, "rows")):
            for i, (s, w) in enumerate(self._resolve(self.w, cols, gu, mu, "columns")):
                cell = self.sub(s, t, w, h, f"{self.name} r{j}c{i}")
                cell.col, cell.row = i, j
                out.append(cell)
        return out

    def __repr__(self):
        o = tuple(round(c, 3) for c in self.origin)
        return f"Frame({self.name!r}, origin={o}, {self.w:.3f} x {self.h:.3f}, n={tuple(round(c, 2) for c in self.n)})"


class Block:
    """What block() built: a box standing on a frame, with a Frame on every
    side so the next part can be attached to it.

    front = the face parallel to the parent frame (for a block standing on a
    floor frame that is its TOP). left/right/top/bottom are the faces at the
    frame's -u/+u/+v/-v edges; one of their axes is the block's depth:
    top/bottom: u = frame u, v = across the depth
    left/right: u = across the depth, v = frame v
    Check with print(block.left) when unsure: Frames print their size."""

    def __init__(self, frame, d0, d1):
        self.frame, self.d0, self.d1 = frame, d0, d1
        f, depth = frame, d1 - d0
        self.front = f.offset(d1, f.name + " front")
        self.back = f.offset(d0).flipped(f.name + " back")
        self.top = Frame(f.at(0, f.h, d1), f.u, -f.n, f.w, depth, f.name + " top")
        self.bottom = Frame(f.at(0, 0, d0), f.u, f.n, f.w, depth, f.name + " bottom")
        self.right = Frame(f.at(f.w, 0, d1), -f.n, f.v, depth, f.h, f.name + " right")
        self.left = Frame(f.at(0, 0, d0), f.n, f.v, depth, f.h, f.name + " left")

    def side(self, name):
        return getattr(self, name)


def _tagged(builder, label):
    if label is not None and hasattr(builder, "tag"):
        builder.tag = label


def block(builder, frame, out, back=EMBED, label=None):
    """Box that covers `frame` exactly and extends `out` metres along its
    normal. It also sinks `back` metres into the parent behind the frame, so
    the two overlap: no gap, no coincident faces. Returns a Block whose
    .front/.top/.bottom/.left/.right frames are where the next part goes."""
    if out + back <= 0:
        raise ValueError(f"block {label or frame.name!r}: out + back must be positive")
    _tagged(builder, label or frame.name)
    depth = out + back
    builder.box(frame.at(frame.w / 2, frame.h / 2, (out - back) / 2), (frame.w, depth, frame.h), frame.basis)
    return Block(frame, -back, out)


def rod(builder, frame, s0, t0, s1, t1, radius, d=None, label=None, segments=12):
    """Cylinder (pipe, rail, conduit) running over a frame from (s0, t0) to
    (s1, t1). By default it sits ON the surface, sunk in by 30 % of its radius."""
    d = radius * 0.7 if d is None else d
    _tagged(builder, label or frame.name + " rod")
    builder.cyl(frame.at(s0, t0, d), frame.at(s1, t1, d), radius, segments=segments)


def outline(builder, frame, radius, margin=0.0, d=None, label=None, segments=10):
    """Four rods around the edge of a frame (neon border, trim, railing),
    overlapping at the corners so they form one connected loop."""
    f, r = frame, radius
    for s0, t0, s1, t1 in ((margin - r, margin, f.w - margin + r, margin),
                           (margin - r, f.h - margin, f.w - margin + r, f.h - margin),
                           (margin, margin - r, margin, f.h - margin + r),
                           (f.w - margin, margin - r, f.w - margin, f.h - margin + r)):
        rod(builder, f, s0, t0, s1, t1, r, d=d, label=label or f.name + " outline", segments=segments)


def text_on(frame, body, material, depth=0.03, margin=0.12, embed=0.01, font=None, name=None,
            collection=None):
    """Extruded text lying on a frame, scaled to fit inside it (minus
    `margin` as a fraction) and sunk `embed` into the surface so it is attached.
    Multi-line `body` ("A\nB") gives vertical signs. Returns the text object."""
    curve = bpy.data.curves.new(name or f"Text {body[:12]}", "FONT")
    curve.body = body
    curve.align_x, curve.align_y = "CENTER", "CENTER"
    curve.extrude = depth / 2
    curve.space_line = 0.85
    if font is not None:
        curve.font = font
    obj = bpy.data.objects.new(curve.name, curve)
    (collection or bpy.context.scene.collection).objects.link(obj)
    curve.materials.append(material)
    bpy.context.view_layer.update()
    dx, dy = max(obj.dimensions.x, 1e-6), max(obj.dimensions.y, 1e-6)
    scale = min(frame.w * (1 - margin) / dx, frame.h * (1 - margin) / dy)
    # centre the text's real bounding box on the frame (fonts are not centred on their origin)
    corners = [Vector(c) for c in obj.bound_box]
    cx = (min(c.x for c in corners) + max(c.x for c in corners)) / 2
    cy = (min(c.y for c in corners) + max(c.y for c in corners)) / 2
    rot = Matrix((frame.u, frame.v, frame.n)).transposed().to_4x4()
    pos = frame.at(frame.w / 2, frame.h / 2, depth * scale / 2 - embed)
    obj.matrix_world = Matrix.Translation(pos) @ rot @ Matrix.Scale(scale, 4) @ Matrix.Translation((-cx, -cy, 0))
    return obj


def footprint_walls(polygon, z0, height, name="wall"):
    """One Frame per side of a CCW 2D polygon (the outer faces of a storey)."""
    n = len(polygon)
    return [Frame.wall(polygon[i], polygon[(i + 1) % n], z0, height, f"{name}{i}") for i in range(n)]


def offset_polygon(polygon, distance):
    """Move every edge of a CCW 2D polygon outward by `distance` (negative =
    inward) and re-intersect neighbours. Use it for slabs, plinths and
    cornices that follow a footprint."""
    n = len(polygon)
    lines = []
    for i in range(n):
        a, b = Vector(polygon[i]), Vector(polygon[(i + 1) % n])
        u = (b - a).normalized()
        lines.append((a + Vector((u.y, -u.x)) * distance, u))
    out = []
    for i in range(n):
        (p1, d1), (p2, d2) = lines[i - 1], lines[i]
        den = d1.x * d2.y - d1.y * d2.x
        if abs(den) < 1e-9:
            out.append((p2.x, p2.y))
            continue
        k = ((p2.x - p1.x) * d2.y - (p2.y - p1.y) * d2.x) / den
        q = p1 + d1 * k
        out.append((q.x, q.y))
    return out


def stack(z0, heights):
    """Cumulative levels: stack(0, [4, 3, 3]) -> [(0, 4), (4, 7), (7, 10)].
    Use it for storeys so each one starts exactly where the last one ends."""
    out = []
    for h in heights:
        out.append((z0, z0 + h))
        z0 += h
    return out


# ====================================================================== audit
_KEEP_BASE = {"BEVEL", "WEIGHTED_NORMAL", "SUBSURF", "EDGE_SPLIT", "TRIANGULATE", "DISPLACE"}


def _islands(n_verts, edges):
    parent = list(range(n_verts))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in edges:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra
    return [find(i) for i in range(n_verts)]


class _Part:
    __slots__ = ("label", "obj", "co", "polys", "lo", "hi", "_bvh")

    def __init__(self, label, obj, co, polys):
        self.label, self.obj, self.co, self.polys = label, obj, co, polys
        self.lo, self.hi = co.min(axis=0), co.max(axis=0)
        self._bvh = None

    @property
    def bvh(self):
        if self._bvh is None:
            self._bvh = BVHTree.FromPolygons([Vector(c) for c in self.co], self.polys, all_triangles=False)
        return self._bvh


def _collect(objects, whole):
    dg = bpy.context.evaluated_depsgraph_get()
    parts = []
    for obj in objects:
        if obj.type not in {"MESH", "CURVE", "FONT", "SURFACE", "META"}:
            continue
        single = obj.type != "MESH" or any(fnmatch.fnmatch(obj.name, p) for p in whole)
        use_eval = obj.type != "MESH" or any(m.type not in _KEEP_BASE for m in obj.modifiers)
        ev = obj.evaluated_get(dg) if use_eval else obj
        me = ev.to_mesh() if use_eval else obj.data
        nv = len(me.vertices)
        if nv == 0 or len(me.polygons) == 0:
            if use_eval:
                ev.to_mesh_clear()
            continue
        co = np.empty(nv * 3, dtype=np.float64)
        me.vertices.foreach_get("co", co)
        mw = np.array(obj.matrix_world)
        co = co.reshape(nv, 3) @ mw[:3, :3].T + mw[:3, 3]
        polys = [tuple(p.vertices) for p in me.polygons]
        if single:
            parts.append(_Part(obj.name, obj.name, co, polys))
        else:
            edges = np.empty(len(me.edges) * 2, dtype=np.int64)
            me.edges.foreach_get("vertices", edges)
            roots = _islands(nv, edges.reshape(-1, 2).tolist())
            tags = json.loads(obj.data.get("dk_parts", "[]")) if not use_eval else []
            groups = {}
            for vi, r in enumerate(roots):
                groups.setdefault(r, []).append(vi)
            face_groups = {}
            for p in polys:
                face_groups.setdefault(roots[p[0]], []).append(p)
            starts = [t[0] for t in tags]
            for k, (r, vis) in enumerate(sorted(groups.items(), key=lambda kv: kv[1][0])):
                if r not in face_groups:
                    continue
                label = f"{obj.name}[{k}]"
                if tags:
                    import bisect
                    idx = bisect.bisect_right(starts, vis[0]) - 1
                    if idx >= 0 and tags[idx][1]:
                        label = f"{tags[idx][1]} ({obj.name})"
                remap = {vi: n for n, vi in enumerate(vis)}
                parts.append(_Part(label, obj.name, co[vis], [tuple(remap[i] for i in p) for p in face_groups[r]]))
        if use_eval:
            ev.to_mesh_clear()
    return parts


def _touching(a, b, tol):
    if a.bvh.overlap(b.bvh):
        return True
    for p, q in ((a, b), (b, a)):
        step = max(1, len(p.co) // 96)
        for c in p.co[::step]:
            if q.bvh.find_nearest(Vector(c), tol)[0] is not None:
                return True
    for p, q in ((a, b), (b, a)):       # p completely inside q (buried part)
        if (p.lo >= q.lo).all() and (p.hi <= q.hi).all():
            origin, hits = Vector(p.co.mean(axis=0)), 0
            direction = Vector((0.0, 0.0, 1.0))
            for _ in range(64):
                loc = q.bvh.ray_cast(origin, direction)[0]
                if loc is None:
                    break
                hits += 1
                origin = loc + direction * 1e-5
            if hits % 2 == 1:
                return True
    return False


def _clip_area(subject, clip):
    """(area, centre, narrow side) of the intersection of two convex 2D polygons."""
    def signed(poly):
        return sum(poly[i][0] * poly[(i + 1) % len(poly)][1] - poly[(i + 1) % len(poly)][0] * poly[i][1]
                   for i in range(len(poly))) / 2
    if signed(clip) < 0:
        clip = clip[::-1]
    out = list(subject)
    for i in range(len(clip)):
        ax, ay = clip[i]
        bx, by = clip[(i + 1) % len(clip)]
        inp, out = out, []
        if not inp:
            return 0.0, (0.0, 0.0), 0.0
        side = [(bx - ax) * (py - ay) - (by - ay) * (px - ax) for px, py in inp]
        for k in range(len(inp)):
            p, q, sp, sq = inp[k], inp[(k + 1) % len(inp)], side[k], side[(k + 1) % len(inp)]
            if sp >= 0:
                out.append(p)
            if (sp > 0 > sq) or (sp < 0 < sq):
                t = sp / (sp - sq)
                out.append((p[0] + (q[0] - p[0]) * t, p[1] + (q[1] - p[1]) * t))
    if len(out) < 3:
        return 0.0, (0.0, 0.0), 0.0
    xs, ys = [p[0] for p in out], [p[1] for p in out]
    return abs(signed(out)), (sum(xs) / len(out), sum(ys) / len(out)), min(max(xs) - min(xs), max(ys) - min(ys))


def _zfight(parts, min_area, max_report, hidden=()):
    hid = [any(fnmatch.fnmatch(p.label, g) for g in hidden) for p in parts]
    buckets = {}
    for pi, part in enumerate(parts):
        co = part.co
        for poly in part.polys:
            if len(poly) < 3:
                continue
            p = co[list(poly)]
            nrm = np.zeros(3)
            for k in range(len(poly)):       # Newell normal, |nrm| = 2 * area
                a, b = p[k], p[(k + 1) % len(poly)]
                nrm += np.cross(a, b)
            area = np.linalg.norm(nrm) / 2
            if area < min_area:
                continue
            nrm /= (area * 2)
            key = (*np.round(nrm, 3), round(float(nrm @ p[0]), 3))
            buckets.setdefault(key, []).append((pi, p, area))
    found = []
    lo = np.array([p.lo for p in parts])
    hi = np.array([p.hi for p in parts])

    def exposed(point, normal):
        """False when the spot is buried inside some part (e.g. the back of a
        block sunk into its wall): step off the surface and test whether that
        point is inside any closed part (ray parity per part)."""
        n = Vector(normal)
        q = Vector(point) + n * 2e-3
        qa = np.array(q)
        for pi in np.flatnonzero(((lo <= qa) & (hi >= qa)).all(axis=1)):
            origin, hits = q.copy(), 0
            for _ in range(64):
                loc = parts[pi].bvh.ray_cast(origin, n)[0]
                if loc is None:
                    break
                hits += 1
                origin = loc + n * 1e-5
            if hits % 2 == 1:
                return False
        return True

    for key, faces in buckets.items():
        if len(faces) < 2 or len({f[0] for f in faces}) < 2:
            continue
        nrm = np.array(key[:3])
        u = np.cross(nrm, [0, 0, 1.0] if abs(nrm[2]) < 0.9 else [1.0, 0, 0])
        u /= np.linalg.norm(u)
        v = np.cross(nrm, u)
        boxes = sorted(((float((p @ u).min()), float((p @ u).max()), float((p @ v).min()), float((p @ v).max()),
                         pi, area, p.mean(axis=0), list(zip((p @ u).tolist(), (p @ v).tolist())))
                        for pi, p, area in faces), key=lambda b: b[0])
        for i, a in enumerate(boxes):
            for b in boxes[i + 1:]:
                if b[0] >= a[1] - 1e-4:
                    break
                if a[4] == b[4] or (hid[a[4]] and hid[b[4]]):
                    continue
                if min(a[3], b[3]) - max(a[2], b[2]) <= 1e-4:
                    continue
                ov, (cu, cv), narrow = _clip_area(a[7], b[7])
                if ov <= max(min_area, 0.05 * min(a[5], b[5])) or narrow < 0.03:
                    continue      # too small to see, or just the strip where a flush part is embedded
                spot = u * cu + v * cv + nrm * key[3]
                if exposed(spot, nrm):
                    found.append({"a": parts[a[4]].label, "b": parts[b[4]].label, "area": round(ov, 4),
                                  "at": tuple(round(float(c), 3) for c in spot)})
                    if len(found) >= max_report:
                        return found
    return found


def _inside_out(part):
    """True for a closed part whose faces point inward (negative volume)."""
    half_edges = sum(len(p) for p in part.polys)
    if half_edges % 2 or len(part.co) - half_edges // 2 + len(part.polys) != 2:
        return False                      # open or non-simple: orientation undefined
    c = part.co - part.co.mean(axis=0)
    vol = 0.0
    for poly in part.polys:
        a = c[poly[0]]
        for k in range(1, len(poly) - 1):
            vol += float(a @ np.cross(c[poly[k]], c[poly[k + 1]]))
    return vol < 0


def audit(objects=None, tol=0.005, anchors=("Ground*", "Road*", "Floor*", "Terrain*", "Street*"),
          allow=(), whole=(), hidden=("*[hidden]*",), zfight=True, min_area=0.005, verbose=True, max_report=25):
    """Check that the scene is actually assembled.

    Every mesh island is a part (MeshBuilder parts keep their labels). Parts
    that overlap or come within `tol` metres are in contact. Reported:

    - "detached": groups of parts with no chain of contact to an anchor
      object (name matching `anchors`; without one, to the largest group).
      Each entry has the gap and the move that would close it.
    - "zfight": coplanar, same-facing, overlapping faces of different parts
      that are visible (overlap larger than `min_area` m2, not buried).
    - "inverted": closed parts whose normals point inward (mirrored transform
      or wrong vertex order): they shade and bevel wrongly.

    allow  = name/label globs that may float on purpose (rain, drones, birds).
    hidden = label globs of parts nobody sees (default: any label containing
             "[hidden]", e.g. interiors behind glass): two hidden parts may
             share a plane without being reported.
    whole  = object globs treated as one part (imported models, particle-like
             meshes). Text/curve objects are always one part.
    Returns {"ok", "parts", "groups", "detached", "zfight", "inverted"}; ok is
    True when all three lists are empty. Run it before every render and fix what it lists."""
    bpy.context.view_layer.update()       # matrix_world is stale right after moving objects
    objects = list(objects) if objects is not None else list(bpy.context.scene.objects)
    objects = [o for o in objects if not o.hide_render]
    parts = _collect(objects, whole)
    n = len(parts)
    if n == 0:
        return {"ok": True, "parts": 0, "groups": 0, "detached": [], "zfight": [], "inverted": []}
    lo = np.array([p.lo for p in parts])
    hi = np.array([p.hi for p in parts])
    parent = list(range(n))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    axis = int(np.argmax(hi.max(axis=0) - lo.min(axis=0)))
    others = [k for k in range(3) if k != axis]
    order = np.argsort(lo[:, axis])
    slo, shi = lo[order], hi[order]
    ends = np.searchsorted(slo[:, axis], shi[:, axis] + tol, side="right")
    for a in range(n):
        if ends[a] <= a + 1:
            continue
        cand = np.arange(a + 1, ends[a])
        m = np.ones(len(cand), dtype=bool)
        for k in others:
            m &= (slo[cand, k] <= shi[a, k] + tol) & (shi[cand, k] >= slo[a, k] - tol)
        ia = int(order[a])
        for b in cand[m]:
            ib = int(order[b])
            ra, rb = find(ia), find(ib)
            if ra != rb and _touching(parts[ia], parts[ib], tol):
                parent[rb] = ra

    groups = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(i)

    def allowed(i):
        return any(fnmatch.fnmatch(parts[i].obj, p) or fnmatch.fnmatch(parts[i].label, p) for p in allow)

    anchored = {r for r, idx in groups.items()
                if any(fnmatch.fnmatch(parts[i].obj, p) for i in idx for p in anchors)}
    if not anchored:
        anchored = {max(groups, key=lambda r: len(groups[r]))}
    detached = []
    for r, idx in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if r in anchored or all(allowed(i) for i in idx):
            continue
        glo, ghi = lo[idx].min(axis=0), hi[idx].max(axis=0)
        mask = np.ones(n, dtype=bool)
        mask[idx] = False
        sep = np.maximum(np.maximum(lo[mask] - ghi, glo - hi[mask]), 0.0)   # per-axis AABB gap
        dist = np.linalg.norm(sep, axis=1)
        k = int(np.argmin(dist))
        near = int(np.flatnonzero(mask)[k])
        move = np.where(lo[near] - ghi > 0, lo[near] - ghi, np.where(glo - hi[near] > 0, hi[near] - glo, 0.0))
        biggest = max(idx, key=lambda i: float(np.prod(np.maximum(hi[i] - lo[i], 1e-6))))
        detached.append({
            "label": parts[biggest].label, "parts": len(idx),
            "center": tuple(round(float(c), 3) for c in (glo + ghi) / 2),
            "size": tuple(round(float(c), 3) for c in ghi - glo),
            "gap": round(float(dist[k]), 4), "nearest": parts[near].label,
            "move_to_touch": tuple(round(float(c), 4) for c in move),
        })
    zf = _zfight(parts, min_area, max_report, hidden) if zfight else []
    inverted = [p.label for p in parts if _inside_out(p)]
    ok = not detached and not zf and not inverted
    if verbose:
        print(f"--- ASSEMBLY AUDIT --- parts={n} groups={len(groups)} detached={len(detached)} "
              f"zfight={len(zf)} inverted={len(inverted)} -> {'OK' if ok else 'FIX BEFORE RENDERING'}")
        if inverted:
            print(f"  INVERTED normals on {len(inverted)} part(s), e.g. {inverted[:5]} "
                  f"(negative scale / mirrored basis: flip the faces)")
        for d in detached[:max_report]:
            how = (f"gap {d['gap']:.3f} m to {d['nearest']!r}, move by {d['move_to_touch']}" if d["gap"] > 0
                   else f"bounding boxes overlap {d['nearest']!r} but surfaces never meet: it is beside/inside it")
            print(f"  DETACHED {d['label']!r} ({d['parts']} part(s)) at {d['center']} size {d['size']}: {how}")
        if len(detached) > max_report:
            print(f"  ... and {len(detached) - max_report} more detached groups")
        for z in zf:
            print(f"  Z-FIGHT {z['a']!r} / {z['b']!r}: {z['area']} m2 coplanar at {z['at']} "
                  f"(offset one by >= 2 mm or embed it)")
    return {"ok": ok, "parts": n, "groups": len(groups), "detached": detached, "zfight": zf,
            "inverted": inverted}
