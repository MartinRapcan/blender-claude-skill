"""
Architecture tools built on assembly.py (Blender 5.x, tested on 5.0.1 / 5.2.3).
See references/assembly.md section 4 and references/detailing.md section 6.

assembly.py is the general part (frames, attachment, audit) and knows nothing
about buildings. This module adds what only walls need:

- wall() / shell(): solid walls WITH their openings, mitred at the corners.
- Opening, window(), plug(), room(): things fitted INTO those openings.
- FacadeKit, facade_segment(), mech_floor(): panelised facades whose cells
  (cladding panels, recessed windows, vents, fins, pipes) are all attached.
- rect(), octagon(): CCW footprints.

    import sys; sys.path.insert(0, "/path/to/skill/blender/scripts")
    import architecture as arch, assembly as asm, detail_kit as dk

The same idea works for any object with holes (a port in a hull, a vent in a
casing): the hole belongs to the parent, the filling is fitted into the hole.
"""

import random

import bpy  # noqa: F401  (before bmesh / mathutils with the pip `bpy` module)

import assembly as asm
import detail_kit as dk
from assembly import EMBED, Frame, _TOL, _tagged, footprint_walls, offset_polygon


# ================================================================= footprints
def rect(hw, hd):
    """CCW rectangle footprint with half width (x) and half depth (y)."""
    return [(hw, -hd), (hw, hd), (-hw, hd), (-hw, -hd)]


def octagon(hw, chamfer):
    """CCW square footprint with chamfered corners."""
    c = chamfer
    return [(hw, -hw + c), (hw, hw - c), (hw - c, hw), (-hw + c, hw),
            (-hw, hw - c), (-hw, -hw + c), (-hw + c, -hw), (hw - c, -hw)]


# ====================================================================== walls
class Opening:
    """A hole made by wall(): .frame is the hole on the outer surface,
    .depth the wall thickness. Fit windows/doors to it, never beside it."""

    def __init__(self, frame, depth):
        self.frame, self.depth, self.name = frame, depth, frame.name

    @property
    def back(self):
        """The hole on the inner surface (same orientation as .frame)."""
        return self.frame.offset(-self.depth, self.name + " back")

    @property
    def sill(self):
        """Bottom reveal, normal up: things standing in the opening go here."""
        f = self.frame
        return Frame(f.at(0, 0, 0), f.u, -f.n, f.w, self.depth, self.name + " sill")

    @property
    def head(self):
        """Top reveal, normal down: blinds, shutters and lights hang here."""
        f = self.frame
        return Frame(f.at(0, f.h, -self.depth), f.u, f.n, f.w, self.depth, self.name + " head")


def _as_rect(frame, item, index):
    if isinstance(item, Frame):
        s, t, d = frame.locate(item.origin)
        if abs(d) > 1e-3 or (item.u - frame.u).length > 1e-3 or (item.v - frame.v).length > 1e-3:
            raise ValueError(f"opening {item.name!r} is not a sub-frame of wall {frame.name!r}: "
                             f"make it with wall_frame.sub()/grid()/centered()")
        return s, t, item.w, item.h, item.name or f"opening{index}"
    s, t, w, h = item[:4]
    return s, t, w, h, (item[4] if len(item) > 4 else f"opening{index}")


def wall(builder, frame, thickness, openings=(), label=None, miter_start=0.0, miter_end=0.0):
    """Solid wall behind `frame` (its outer surface) with real holes.

    One watertight mesh island: outer face, inner face and the reveals of
    every hole. `openings` are sub-frames of `frame` (preferred) or
    (s, t, w, h) tuples. Raises if an opening leaves the wall or two overlap.
    miter_start/miter_end shorten the INNER face by that many metres at each
    end, so walls meeting at a corner share a mitre plane instead of both
    filling the corner (shell() computes these for any polygon).
    Returns a list of Opening in the same order."""
    rects = [_as_rect(frame, o, i) for i, o in enumerate(openings)]
    for s, t, w, h, name in rects:
        if s < max(miter_start, 0) + _TOL or s + w > frame.w - max(miter_end, 0) - _TOL:
            raise ValueError(f"opening {name!r} is inside the corner of wall {frame.name!r}: "
                             f"keep openings at least the wall thickness away from the ends")
    for s, t, w, h, name in rects:
        if s < -_TOL or t < -_TOL or s + w > frame.w + _TOL or t + h > frame.h + _TOL:
            raise ValueError(f"opening {name!r} leaves wall {frame.name!r} ({frame.w:.2f} x {frame.h:.2f})")
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if a[0] < b[0] + b[2] - _TOL and b[0] < a[0] + a[2] - _TOL and \
               a[1] < b[1] + b[3] - _TOL and b[1] < a[1] + a[3] - _TOL:
                raise ValueError(f"openings {a[4]!r} and {b[4]!r} overlap in wall {frame.name!r}")

    def cuts(lo_hi, total):
        vals = sorted({0.0, total, *lo_hi})
        out = [vals[0]]
        for x in vals[1:]:
            if x - out[-1] > _TOL:
                out.append(x)
        return out

    ss = cuts([min(max(v, 0.0), frame.w) for r in rects for v in (r[0], r[0] + r[2])], frame.w)
    ts = cuts([min(max(v, 0.0), frame.h) for r in rects for v in (r[1], r[1] + r[3])], frame.h)
    ni, nj = len(ss) - 1, len(ts) - 1

    def solid(i, j):
        if i < 0 or j < 0 or i >= ni or j >= nj:
            return False
        cs, ct = (ss[i] + ss[i + 1]) / 2, (ts[j] + ts[j + 1]) / 2
        return not any(r[0] < cs < r[0] + r[2] and r[1] < ct < r[1] + r[3] for r in rects)

    _tagged(builder, label or frame.name or "wall")
    if hasattr(builder, "_mark"):
        builder._mark()
    bm, cache = builder.bm, {}

    def vert(i, j, layer):
        key = (i, j, layer)
        if key not in cache:
            s = ss[i]
            if layer and i == 0:
                s += miter_start
            elif layer and i == ni:
                s -= miter_end
            cache[key] = bm.verts.new(frame.at(s, ts[j], -thickness * layer))
        return cache[key]

    for i in range(ni):
        for j in range(nj):
            if not solid(i, j):
                continue
            bm.faces.new((vert(i, j, 0), vert(i + 1, j, 0), vert(i + 1, j + 1, 0), vert(i, j + 1, 0)))
            bm.faces.new((vert(i, j + 1, 1), vert(i + 1, j + 1, 1), vert(i + 1, j, 1), vert(i, j, 1)))
            if not solid(i + 1, j):
                bm.faces.new((vert(i + 1, j, 0), vert(i + 1, j, 1), vert(i + 1, j + 1, 1), vert(i + 1, j + 1, 0)))
            if not solid(i - 1, j):
                bm.faces.new((vert(i, j + 1, 0), vert(i, j + 1, 1), vert(i, j, 1), vert(i, j, 0)))
            if not solid(i, j + 1):
                bm.faces.new((vert(i, j + 1, 0), vert(i + 1, j + 1, 0), vert(i + 1, j + 1, 1), vert(i, j + 1, 1)))
            if not solid(i, j - 1):
                bm.faces.new((vert(i, j, 1), vert(i + 1, j, 1), vert(i + 1, j, 0), vert(i, j, 0)))
    builder.count += 1
    return [Opening(frame.sub(s, t, w, h, name), thickness) for s, t, w, h, name in rects]


def window(frames, glass, opening, profile=0.06, setback=0.08, depth=0.07, cols=1, rows=1,
           sill=0.05, embed=0.01, label=None):
    """Window that fills `opening` exactly: frame members on all four edges
    (sunk `embed` into the reveals), cols x rows panes divided by mullions,
    one glass sheet caught in the members, and a sill under the opening.
    `frames` and `glass` are builders. Returns the Frame of the glass plane."""
    f, name = opening.frame, label or opening.name
    depth = min(depth, opening.depth - setback)
    if depth <= 0:
        raise ValueError(f"window {name!r}: setback {setback} is deeper than the wall ({opening.depth})")
    if 2 * profile >= min(f.w, f.h):
        raise ValueError(f"window {name!r}: profile {profile} too big for opening {f.w:.2f} x {f.h:.2f}")
    dc = -setback - depth / 2

    def member(s0, t0, s1, t1, tag):
        _tagged(frames, f"{name} {tag}")
        frames.box(f.at((s0 + s1) / 2, (t0 + t1) / 2, dc), (s1 - s0, depth, t1 - t0), f.basis)

    member(-embed, -embed, profile, f.h + embed, "jamb L")
    member(f.w - profile, -embed, f.w + embed, f.h + embed, "jamb R")
    member(profile - embed, -embed, f.w - profile + embed, profile, "bottom rail")
    member(profile - embed, f.h - profile, f.w - profile + embed, f.h + embed, "head")
    for i in range(1, cols):
        s = f.w * i / cols
        member(s - profile / 2, profile - embed, s + profile / 2, f.h - profile + embed, f"mullion {i}")
    for j in range(1, rows):
        t = f.h * j / rows
        member(profile - embed, t - profile * 0.4, f.w - profile + embed, t + profile * 0.4, f"transom {j}")
    _tagged(glass, f"{name} glass")
    glass.box(f.at(f.w / 2, f.h / 2, dc), (f.w - 2 * profile + 2 * embed, 0.012, f.h - 2 * profile + 2 * embed),
              f.basis)
    if sill:
        _tagged(frames, f"{name} sill")
        frames.box(f.at(f.w / 2, -sill / 2 + embed, (sill - setback) / 2),
                   (f.w + 2 * sill, sill + setback, sill), f.basis)
    return f.offset(dc, name + " glass")


def plug(builder, opening, setback=0.1, thickness=0.05, embed=0.01, label=None):
    """Flat panel that closes an opening (door leaf, shutter, board, screen),
    sunk `embed` into the reveals on all sides. Returns the Frame of its face."""
    f = opening.frame
    if setback + thickness > opening.depth + _TOL:
        raise ValueError(f"plug {label or opening.name!r} does not fit in a wall {opening.depth} m thick")
    _tagged(builder, label or opening.name + " plug")
    builder.box(f.at(f.w / 2, f.h / 2, -setback - thickness / 2),
                (f.w + 2 * embed, thickness, f.h + 2 * embed), f.basis)
    return f.offset(-setback, (label or opening.name) + " face")


def room(builder, opening, depth=1.2, margin=0.05, label=None):
    """Open-fronted box behind an opening (the room you see through the
    glass). Its rim overlaps the inner wall face, so it is attached. Use an
    emissive/interior material on `builder`. Returns the Frame of its back wall."""
    f = opening.frame
    name = label or opening.name + " room [hidden]"
    _tagged(builder, name)
    if hasattr(builder, "_mark"):
        builder._mark()
    d0, d1 = -opening.depth + EMBED, -opening.depth - depth
    s0, s1, t0, t1 = -margin, f.w + margin, -margin, f.h + margin
    bm = builder.bm
    v = {(a, b, c): bm.verts.new(f.at(s, t, d))
         for a, s in enumerate((s0, s1)) for b, t in enumerate((t0, t1)) for c, d in enumerate((d0, d1))}
    for quad in (((0, 0, 1), (1, 0, 1), (1, 1, 1), (0, 1, 1)),      # back
                 ((0, 0, 0), (0, 0, 1), (0, 1, 1), (0, 1, 0)),      # left
                 ((1, 0, 1), (1, 0, 0), (1, 1, 0), (1, 1, 1)),      # right
                 ((0, 0, 0), (1, 0, 0), (1, 0, 1), (0, 0, 1)),      # floor
                 ((0, 1, 1), (1, 1, 1), (1, 1, 0), (0, 1, 0))):     # ceiling
        bm.faces.new([v[k] for k in quad])
    builder.count += 1
    return Frame(f.at(s0, t0, d1), f.u, f.v, s1 - s0, t1 - t0, name + " back")


def shell(builder, polygon, z0, height, thickness, openings=None, name="wall"):
    """All walls of one storey of a CCW footprint (any angles), mitred at the
    corners so they meet without overlapping. `openings(i, frame)` returns the
    openings (sub-frames) for wall i, or None. Returns (frames, openings_per_wall)."""
    frames = footprint_walls(polygon, z0, height, name)
    inner = offset_polygon(polygon, -thickness)
    n, result = len(polygon), []
    for i, f in enumerate(frames):
        ms = f.locate((*inner[i], z0))[0]
        me = f.w - f.locate((*inner[(i + 1) % n], z0))[0]
        ops = (openings(i, f) if openings else None) or ()
        result.append(wall(builder, f, thickness, ops, miter_start=ms, miter_end=me))
    return frames, result


# ================================================================ facade kit
class FacadeKit:
    """Builders + materials for panelised facades. Pass `materials` to restyle
    (keys: panels, core, frames, glass, warm, cool, dim, off, blinds, pipes,
    vents, light_warm, light_cool, light_red, strip)."""

    def __init__(self, seed=0, skin=0.4, materials=None, prefix=""):
        self.rng = random.Random(seed)
        self.skin = skin  # wall thickness: windows are recessed into it
        mats = {
            "panels": dk.panel_metal(prefix + "PanelMetal"),
            "core": dk.simple(prefix + "Core", (0.008, 0.008, 0.01), rough=0.7),
            "frames": dk.simple(prefix + "Frames", (0.03, 0.032, 0.035), rough=0.35, metal=0.8),
            "glass": dk.glass(prefix + "Glass"),
            "warm": dk.interior(prefix + "RoomWarm", (1.0, 0.62, 0.3), 3.5),
            "cool": dk.interior(prefix + "RoomCool", (0.55, 0.75, 1.0), 2.5),
            "dim": dk.interior(prefix + "RoomDim", (1.0, 0.7, 0.45), 0.5),
            "off": dk.simple(prefix + "RoomOff", (0.01, 0.01, 0.012), rough=0.8),
            "blinds": dk.simple(prefix + "Blinds", (0.5, 0.48, 0.45), rough=0.5),
            "pipes": dk.simple(prefix + "Pipes", (0.06, 0.065, 0.07), rough=0.4, metal=1.0),
            "vents": dk.simple(prefix + "Vents", (0.015, 0.015, 0.017), rough=0.5, metal=0.7),
            "light_warm": dk.emissive(prefix + "LightWarm", (1.0, 0.75, 0.45), 60),
            "light_cool": dk.emissive(prefix + "LightCool", (0.75, 0.9, 1.0), 50),
            "light_red": dk.emissive(prefix + "LightRed", (1.0, 0.05, 0.02), 80),
            "strip": dk.emissive(prefix + "Strip", (0.9, 0.95, 1.0), 12),
        }
        mats.update(materials or {})
        bevels = {"panels": 0.025, "frames": 0.01, "blinds": 0.005, "vents": 0.008}
        self.b = {k: dk.MeshBuilder(prefix + k.title(), m, bevels.get(k, 0.0)) for k, m in mats.items()}
        self.rooms = {k: self.b[k] for k in ("warm", "cool", "dim", "off")}

    def __getattr__(self, key):  # kit.panels, kit.frames, ... -> builders
        b = self.__dict__.get("b", {})
        if key in b:
            return b[key]
        raise AttributeError(key)

    def finish(self):
        """Create one object per material. Returns the list of objects."""
        return [o for o in (mb.finish() for mb in self.b.values()) if o]

    # ------------------------------------------------------------ cells
    # Every cell is ATTACHED: the wall is a real slab with holes (assembly.wall),
    # windows are fitted into those holes, panels and equipment stand on the
    # wall surface and sink 2 cm into it. assembly.audit() must report nothing.
    def window(self, opening, lit_weights=(30, 12, 18, 40)):
        """Fit a window into an assembly.Opening: frame, mullion, glass, sill,
        casing on the wall around it, a room behind (lit or dark) with a
        ceiling light, and blinds caught between the reveals in some."""
        rng, f = self.rng, opening.frame
        window(self.frames, self.glass, opening, profile=0.07, setback=0.1, depth=0.08,
                   cols=2, rows=2 if rng.random() < 0.4 else 1, sill=0.06)
        state = rng.choices(["warm", "cool", "dim", "off"], weights=lit_weights)[0]
        room(self.rooms[state], opening, depth=1.0)
        if state in ("warm", "cool"):
            asm.block(self.light_warm, opening.head.centered(f.w * 0.6, 0.1, t=opening.depth * 0.75),
                      out=0.03, label=f"{opening.name} ceiling light")
        if state != "off" and rng.random() < 0.45:
            slat, d = 0.09, -0.26
            for k in range(int(f.h * rng.uniform(0.2, 0.9) / slat)):
                self.blinds.tag = f"{opening.name} blind"
                self.blinds.box(f.at(f.w / 2, f.h - 0.04 - k * slat, d), (f.w + 2 * asm.EMBED, 0.06, 0.012), f.basis)
        return state

    def panel(self, cell, gap=0.07, light_prob=0.16):
        """Cladding panel standing on the wall inside `cell` (seams show the dark
        wall), random relief, sometimes split, with hatches, louvres, lights."""
        rng = self.rng
        depth = 0.22 + (0.25 if rng.random() < 0.12 else rng.uniform(0, 0.08))
        area = cell.inset(gap / 2)
        halves = area.columns(2, gap=gap) if rng.random() < 0.35 else [area]
        blocks = [asm.block(self.panels, h, out=depth, label=cell.name + " panel") for h in halves]
        front = blocks[0].front if len(blocks) == 1 else area.offset(depth)
        r = rng.random()
        if r < 0.08 and len(blocks) == 1:
            hatch = asm.block(self.vents, front.centered(front.w * 0.4, front.h * 0.5), out=0.03,
                              label=cell.name + " hatch")
            asm.block(self.frames, hatch.front.centered(0.04, 0.2, s=hatch.front.w * 0.8), out=0.04,
                      label=cell.name + " handle")
        elif r < 0.14 and len(blocks) == 1:
            grill = front.centered(front.w * 0.5, min(0.7, front.h * 0.6))
            for row in grill.rows(6, gap=0.06):
                asm.block(self.vents, row, out=0.03, label=cell.name + " louvre")
        if rng.random() < light_prob:
            lights = self.light_warm if rng.random() < 0.7 else self.light_cool
            b = blocks[0].front
            lights.tag = cell.name + " light"
            lights.dot(b.at(0.15 if rng.random() < 0.5 else b.w - 0.15, b.h - 0.15, 0.0), 0.05)

    def vent_bank(self, cell):
        """Louvred housing standing on the wall."""
        box = asm.block(self.vents, cell.inset(0.05), out=0.2, label=cell.name + " vent")
        face = box.front.inset(0.1)
        for row in face.rows(max(2, int(face.h / 0.15)), gap=0.07):
            asm.block(self.vents, row, out=0.05, label=cell.name + " louvre")

    def fin(self, f, s, light_every=3.0, light_prob=0.75, width=0.35, out=1.2):
        """Full-height vertical fin on the wall with small lights on its edge."""
        b = asm.block(self.panels, f.sub(s - width / 2, 0.1, width, f.H - 0.2), out=out, label=f.name + " fin")
        for k in range(int(f.H / light_every)):
            if self.rng.random() < light_prob and light_every / 2 + k * light_every < b.front.h:
                self.light_warm.tag = f.name + " fin light"
                self.light_warm.dot(b.front.at(width / 2, light_every / 2 + k * light_every, 0.0), 0.07)

    def pipe_run(self, f, t, d, radius, flange_every=4.0, margin=0.3):
        """Horizontal pipe `d` metres off a wall, carried by brackets that
        start in the wall, with flanges. Ends stay `margin` inside the wall's
        ends so runs on neighbouring walls don't cross at corners."""
        asm.rod(self.pipes, f, margin, t, f.L - margin, t, radius, d=d, label=f.name + " pipe", segments=16)
        s = min(flange_every / 2, f.L / 2)
        while s < f.L - margin:
            self.pipes.tag = f.name + " flange"
            self.pipes.cyl(f.at(s - 0.08, t, d), f.at(s + 0.08, t, d), radius * 1.25, segments=16)
            asm.block(self.vents, f.centered(0.15, 0.1, s=s + 0.3, t=t), out=d, label=f.name + " bracket")
            s += flange_every

    def plan(self, f, col_w=3.0, row_h=2.0, window_ratio=0.25, strip_prob=0.25, vent_ratio=0.05,
             corner=0.0):
        """Decide what goes in each cell of a wall: [(kind, cell Frame)].
        Structure beats noise: whole columns become glazing strips
        (`strip_prob`), the rest is mostly panels with some windows and vents.
        `corner` keeps windows that far from the wall's ends (mitred corners)."""
        rng = self.rng
        nc, nr = max(1, round(f.L / col_w)), max(1, round(f.H / row_h))
        strips = {i for i in range(nc) if rng.random() < strip_prob}
        cells = []
        for cell in f.grid(nc, nr):
            r = rng.random()
            at_corner = cell.col == 0 or cell.col == nc - 1
            if ((cell.col in strips and cell.row % 2 == 1) or r < window_ratio * 0.4) and \
                    not (at_corner and corner > 0.05) and cell.w > 0.6 and cell.h > 0.8:
                cells.append(("window", cell))
            elif r < window_ratio * 0.4 + vent_ratio:
                cells.append(("vent", cell))
            else:
                cells.append(("panel", cell))
        return cells

    def build_wall(self, f, cells, miter=(0.0, 0.0), fins_every=3):
        """Build one wall from a plan: the slab with a hole per window cell,
        then every cell attached to it."""
        holes = [cell.inset(0.14, 0.14, 0.16, 0.14, cell.name + " window") for kind, cell in cells if kind == "window"]
        openings = iter(wall(self.core, f, self.skin, holes, label=f.name,
                                 miter_start=miter[0], miter_end=miter[1]))
        for kind, cell in cells:
            if kind == "window":
                o = next(openings)
                self.window(o)
                rim = 0.1                                   # casing around the hole, on the wall
                for strip in (cell.sub(0.02, cell.h - 0.12, cell.w - 0.04, rim),          # head
                              cell.sub(0.02, 0.02, cell.w - 0.04, rim + 0.02),            # under the sill
                              cell.sub(0.02, 0.14, rim, cell.h - 0.26),                   # jambs, between them
                              cell.sub(cell.w - 0.12, 0.14, rim, cell.h - 0.26)):
                    asm.block(self.panels, strip, out=0.12, label=cell.name + " casing")
            elif kind == "vent":
                self.vent_bank(cell)
            else:
                self.panel(cell)
        nc = max(c.col for _, c in cells) + 1
        if fins_every and f.L > 10:
            for i in range(fins_every, nc, fins_every):
                self.fin(f, f.L * i / nc)

    def panelize(self, f, fins_every=3, miter=(0.0, 0.0), **plan_args):
        """plan() + build_wall() for one wall frame."""
        self.build_wall(f, self.plan(f, corner=max(miter), **plan_args), miter, fins_every)


def facade_segment(kit, polygon, z0, z1, slab=0.5, **panelize_args):
    """One storey block on a CCW footprint: mitred walls with window holes,
    every wall panelised, and a floor slab on top that overhangs the walls,
    with a light strip on its edge."""
    h = z1 - z0 - (slab or 0.0)
    inner = asm.offset_polygon(polygon, -kit.skin)
    n = len(polygon)
    fins = panelize_args.pop("fins_every", 3)
    for i, f in enumerate(asm.footprint_walls(polygon, z0, h, f"z{z0:g} wall")):
        ms = f.locate((*inner[i], z0))[0]
        me = f.w - f.locate((*inner[(i + 1) % n], z0))[0]
        kit.panelize(f, fins_every=fins, miter=(ms, me), **panelize_args)
    if slab:
        edge = asm.offset_polygon(polygon, 0.45)
        kit.panels.tag = f"z{z0:g} slab"
        kit.panels.prism(edge, z1 - slab, z1)
        for f in asm.footprint_walls(edge, z1 - slab, slab, f"z{z0:g} slab edge"):
            if f.w > 1.0:
                asm.block(kit.strip, f.centered(f.w * 0.9, 0.05, t=0.12), out=0.02, label=f.name + " strip")


def mech_floor(kit, polygon, z0, z1, max_out=1.6):
    """Recessed plant floor: mitred walls, pipe runs on brackets, vent banks
    and small lights. Pass a polygon 2-3 m smaller than the storeys around it;
    nothing sticks out more than `max_out` metres. Pipe sizes scale with the
    floor height."""
    frames, _ = shell(kit.core, polygon, z0, z1 - z0, kit.skin, name=f"z{z0:g} plant wall")
    for f in frames:
        if f.L < 6:
            continue
        for r, d, t in ((0.06, 0.35, 0.22), (0.035, 0.7, 0.45), (0.08, 0.95, 0.68), (0.03, 0.3, 0.86)):
            radius = r * f.H
            kit.pipe_run(f, t * f.H, min(d * max_out, max_out - radius), radius)
        s = 4.3                                   # between the pipe brackets (2.3, 6.3, 10.3, ...)
        while s < f.L - 2.5:
            kit.vent_bank(f.centered(2.2, f.H * 0.3, s=s, t=f.H * 0.57))
            s += 8.0
        for k in range(1, int(f.L), 2):
            if kit.rng.random() < 0.5:
                kit.light_warm.tag = f.name + " light"
                kit.light_warm.dot(f.at(k, f.H - 0.3, 0.0), 0.07)
