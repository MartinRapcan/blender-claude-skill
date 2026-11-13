"""
Worked example: rainy neon street at night (original design), built entirely by
ATTACHMENT with scripts/assembly.py + architecture.py, checked with assembly.audit().

    blender -b --factory-startup --python-exit-code 1 -P neon_street.py -- \
        --out street.png --res 1280x720 --samples 48 [--blend street.blend]

Read this when a scene has several buildings or many parts that must meet:
- one layout (constants + stack/columns/grid), no hand-typed child coordinates;
- walls are real slabs with holes (asm.shell), windows are fitted into the
  holes, signs/canopies/AC units/pipes stand on wall frames and sink into them;
- every builder call returns frames that the next layer is placed on;
- audit() must come back clean before the render.
"""
import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
from boilerplate import *            # noqa: E402,F401
import architecture as arch          # noqa: E402
import assembly as asm               # noqa: E402
import detail_kit as dk              # noqa: E402
import lighting_presets as lp        # noqa: E402
from architecture import plug, room, window                     # noqa: E402
from assembly import Frame, block, outline, rod, text_on        # noqa: E402
from mathutils import Vector         # noqa: E402

rng = random.Random(11)

# ------------------------------------------------------------------ layout
ROAD_W, WALK_W, CURB = 9.0, 3.2, 0.15
XF = ROAD_W / 2 + WALK_W               # facade line, both sides
LENGTH = 96.0                          # street runs from y = 0 to LENGTH
DEPTH = 12.0                           # building depth behind the facade
GROUND_H, FLOOR_H, WALL_T = 4.3, 3.2, 0.3
NEON = {"pink": (1.0, 0.06, 0.45), "cyan": (0.0, 0.85, 1.0), "amber": (1.0, 0.42, 0.05),
        "violet": (0.55, 0.15, 1.0), "green": (0.1, 1.0, 0.45), "red": (1.0, 0.08, 0.06)}
WORDS = ["NOVA", "KIRU", "ORBITAL", "RAMEN 9", "HOTEL LUX", "ZEN", "VOLT", "MIRA", "BAR 77",
         "SYNTH", "TOKI", "LUNA", "ARCADE", "NOODLE", "ECHO", "PIXEL"]


# --------------------------------------------------------------- builders
class Kit:
    """All shared builders (one mesh per material at the end)."""

    def __init__(self):
        mb = dk.MeshBuilder
        self.metal = mb("Metal", dk.panel_metal("DarkMetal", tone=(0.02, 0.05)), bevel=0.006)
        self.dark = mb("SignDark", dk.simple("SignDark", (0.012, 0.012, 0.015), rough=0.35), bevel=0.006)
        self.frames = mb("WindowFrames", dk.simple("FrameMetal", (0.03, 0.032, 0.035), rough=0.4, metal=0.8))
        self.glass = mb("Glass", dk.glass())
        self.blinds = mb("Blinds", dk.simple("Blinds", (0.4, 0.38, 0.35), rough=0.6))
        self.doors = mb("Doors", dk.worn_paint("DoorPaint", paint=(0.08, 0.2, 0.22), wear=0.3), bevel=0.004)
        self.pipes = mb("Pipes", dk.simple("PipeMetal", (0.08, 0.08, 0.09), rough=0.35, metal=1.0))
        self.concrete = mb("Sidewalk", dk.concrete("SidewalkConcrete", tone=(0.07, 0.11)), bevel=0.01)
        self.paint = mb("RoadPaint", dk.simple("RoadPaint", (0.5, 0.5, 0.45), rough=0.5))
        self.rooms = {"warm": mb("RoomsWarm", dk.interior("RoomWarm", (1.0, 0.6, 0.28), 2.5)),
                      "cool": mb("RoomsCool", dk.interior("RoomCool", (0.5, 0.75, 1.0), 2.0)),
                      "tv": mb("RoomsTV", dk.interior("RoomTV", (0.6, 0.3, 1.0), 1.2)),
                      "off": mb("RoomsOff", dk.simple("RoomOff", (0.01, 0.01, 0.012), rough=0.8))}
        self.shops = {c: mb(f"Shop{c.title()}", dk.interior(f"Shop{c}", NEON[c], 1.6)) for c in ("cyan", "amber", "pink")}
        self.neon = {c: mb(f"Neon{c.title()}", dk.emissive(f"Neon{c}", rgb, 14)) for c, rgb in NEON.items()}
        self.text = {c: dk.emissive(f"Text{c}", rgb, 16) for c, rgb in NEON.items()}
        self.lamp = mb("LampHeads", dk.emissive("LampWhite", (0.8, 0.9, 1.0), 12))
        self.walls = []

    def wall_builder(self, i):
        tone = rng.choice([(0.035, 0.06), (0.05, 0.09), (0.025, 0.045)])
        tint = rng.choice([(1.0, 0.95, 0.9), (0.9, 0.97, 1.05), (1.0, 1.0, 1.0)])
        b = dk.MeshBuilder(f"Walls{i}", dk.concrete(f"Wall{i}", tone=tone, tint=tint))
        self.walls.append(b)
        return b

    def finish(self):
        for b in [self.metal, self.dark, self.frames, self.glass, self.blinds, self.doors, self.pipes,
                  self.concrete, self.paint, self.lamp, *self.rooms.values(), *self.shops.values(),
                  *self.neon.values(), *self.walls]:
            b.finish()


# ---------------------------------------------------------------- street
def build_ground(kit):
    g = dk.MeshBuilder("Ground", dk.wet_asphalt())
    center, size = (0, LENGTH / 2 + 110, -0.25), (360, LENGTH + 300, 0.5)   # reaches under the skyline
    g.box(center, size)
    g.finish()
    top = Frame.of_box(center, size, "+Z", "ground")          # u = +X, v = +Y, origin at (xmin, ymin)
    x0, y0 = top.locate((0, 0, 0))[:2]                         # frame coords of the world origin

    walks = {}
    for side, s in ((-1, x0 - XF), (1, x0 + XF - WALK_W)):
        walks[side] = block(kit.concrete, top.sub(s, y0 - 20, WALK_W, LENGTH + 20, f"sidewalk {side}"),
                            out=CURB).front
    for k in range(int(LENGTH / 6)):                           # centre line dashes
        block(kit.paint, top.sub(x0 - 0.07, y0 + 1 + k * 6, 0.14, 2.4, "lane dash"), out=0.004)
    for k in range(7):                                         # zebra crossing near the camera
        block(kit.paint, top.sub(x0 - 3.6 + k * 1.1, y0 + 4.0, 0.55, 2.6, "zebra"), out=0.004)
    return top, walks, (x0, y0)


def street_lamp(kit, walk, t, side):
    s = 0.45 if side > 0 else walk.w - 0.45                    # at the kerb edge
    kit.pipes.tag = "lamp post"
    kit.pipes.cyl(walk.at(s, t, -asm.EMBED), walk.at(s, t, 5.6), 0.07)
    reach = -1.5 * side                                        # arm over the road
    kit.pipes.cyl(walk.at(s, t, 5.5), walk.at(s + reach, t, 5.5), 0.045)
    kit.lamp.tag = "lamp head"
    kit.lamp.box(walk.at(s + reach * 0.75, t, 5.44), (0.7, 0.22, 0.06))
    kit.pipes.cyl(walk.at(s, t, 0.0), walk.at(s, t, 0.5), 0.11)            # base sleeve


def props(kit, walk, side):
    ty = lambda y: walk.locate((0, y, 0))[1]                   # world y -> position along the sidewalk  # noqa: E731
    for y in range(6, int(LENGTH), 16):
        street_lamp(kit, walk, ty(y + (8 if side > 0 else 0)), side)
    kerb = 0.8 if side > 0 else walk.w - 0.8
    for y in range(3, 60, 4):                                  # bollards
        kit.pipes.tag = "bollard"
        kit.pipes.cyl(walk.at(kerb, ty(y + 1.0), -asm.EMBED), walk.at(kerb, ty(y + 1.0), 0.8), 0.07)
        block(kit.neon["amber"], walk.centered(0.1, 0.1, s=kerb, t=ty(y + 1.0)).offset(0.8), out=0.03,
              label="bollard cap")
    wall_side = walk.w - 0.55 if side > 0 else 0.55            # against the facades
    for y in (9.5, 30.5, 47.0):
        t = ty(y + 3 * side)
        vm = block(kit.dark, walk.centered(0.8, 1.0, s=wall_side, t=t), out=1.9, label="vending machine")
        face = vm.left if side > 0 else vm.right               # the side that looks at the street (u = up)
        block(kit.neon[rng.choice(["cyan", "pink", "green"])], face.inset(0.7, 0.12, 0.08, 0.08), out=0.015,
              label="vending screen")
        block(kit.metal, face.inset(0.12, 1.35, 0.12, 0.12), out=0.03, label="vending tray")
        bin_ = block(kit.metal, walk.centered(0.5, 0.5, s=wall_side, t=t + 1.4), out=0.95, label="bin")
        block(kit.dark, bin_.front.inset(0.06), out=0.03, label="bin lid")


# -------------------------------------------------------------- buildings
def wall_sign(kit, sf, colour, word):
    """Horizontal neon sign on the spandrel under a row of windows."""
    board = block(kit.dark, sf.centered(min(sf.w - 2.0, 1.1 * len(word) + 1.0), 0.62, t=0.47, name="wall sign"), out=0.1)
    text_on(board.front, word, kit.text[colour], depth=0.04)
    outline(kit.neon[colour], board.front, 0.016, margin=0.04, label="wall sign border")


def upper_window(kit, wall_frame, o, ac=True):
    window(kit.frames, kit.glass, o, cols=2, rows=rng.choice((1, 1, 2)), profile=0.06, sill=0.06)
    state = rng.choices(["warm", "cool", "tv", "off"], weights=[30, 14, 10, 46])[0]
    room(kit.rooms[state], o, depth=1.0)
    if state != "off" and rng.random() < 0.5:                  # blinds caught between the reveals
        for k in range(int(o.frame.h * rng.uniform(0.2, 0.8) / 0.09)):
            kit.blinds.tag = o.name + " blind"
            kit.blinds.box(o.frame.at(o.frame.w / 2, o.frame.h - 0.05 - k * 0.09, -0.24),
                           (o.frame.w + 2 * asm.EMBED, 0.05, 0.012), o.frame.basis)
    if ac and rng.random() < 0.35:                             # AC unit under the window, on the wall
        ac = block(kit.metal, o.frame.rel(0.15, -0.72, 0.85, 0.52, o.name + " AC"), out=0.42)
        for row in ac.front.inset(0.06).rows(6, gap=0.03):
            block(kit.dark, row, out=0.012, label=o.name + " AC grille")
        rod(kit.pipes, o.frame.rel(0.95, -0.9, 0.2, 0.2), 0.1, 0.0, 0.1, 0.45, 0.02, label=o.name + " AC pipe")


def shopfront(kit, o_shop, o_door, colour, word):
    shop = rng.choice(list(kit.shops))
    window(kit.frames, kit.glass, o_shop, cols=3, rows=1, profile=0.07, setback=0.12, sill=0.0)
    back = room(kit.shops[shop], o_shop, depth=3.0)
    for shelf in back.inset(0.3, 0.3, 0.5, 0.9).rows(3, gap=0.45):         # shelves on the back wall
        block(kit.dark, shelf.inset(0, 0, 0, shelf.h - 0.06), out=0.35, label="shop shelf")
    outline(kit.neon[colour], o_shop.frame, 0.025, margin=-0.1, label="shop neon")
    leaf = plug(kit.doors, o_door, setback=0.12, thickness=0.06)
    block(kit.frames, leaf.centered(0.05, 0.3, s=leaf.w * 0.82, t=1.05), out=0.05, back=0.01, label="door handle")
    block(kit.glass, leaf.inset(0.2, 0.2, 1.3, 0.25), out=0.008, back=0.005, label="door glass")
    block(kit.neon["amber"], o_door.frame.rel(0.1, o_door.frame.h + 0.12, o_door.frame.w - 0.2, 0.06), out=0.05,
          label="door light")
    board = block(kit.dark, o_shop.frame.rel(0, o_shop.frame.h + 0.2, o_shop.frame.w, 0.6, "sign board"), out=0.1)
    text_on(board.front, word, kit.text[colour], depth=0.04)
    outline(kit.neon[colour], board.front, 0.018, margin=0.05, label="sign border")


def canopy(kit, face, s, w, colour):
    c = block(kit.metal, face.sub(s, GROUND_H - 0.5, w, 0.16, "canopy"), out=1.7)
    for rib in c.front.columns(max(2, int(w / 0.5)), gap=0.3):
        block(kit.dark, rib.inset(0, 0, 0.03, 0.03), out=0.03, label="canopy rib")
    block(kit.neon[colour], c.bottom.inset(0.15, 0.15, 1.25, 0.3), out=0.03, label="canopy light")
    for k in (0.12, 0.88):                                     # tie rods from the wall to the canopy edge
        kit.pipes.tag = "canopy tie"
        kit.pipes.cyl(face.at(s + w * k, GROUND_H + 0.9, -0.02), c.top.at(w * k, 0.2, -0.02), 0.02)


def blade_sign(kit, face, s, t0, height, colour, word):
    """Vertical sign standing out from the wall on two arms."""
    for t in (t0 + 0.35, t0 + height - 0.35):
        block(kit.metal, face.centered(0.08, 0.08, s=s, t=t, name="sign arm"), out=1.3)
    plate = Frame(face.at(s, t0, 0.25), face.n, face.v, 1.0, height, "blade sign")   # edge-on to the wall
    b = block(kit.dark, plate, out=0.07, back=0.07)
    for fr in (b.front, b.back):
        text_on(fr.inset(0.1), "\n".join(word.replace(" ", "")), kit.text[colour], depth=0.03)
        outline(kit.neon[colour], fr, 0.018, margin=0.05, label="blade border")


def building(kit, idx, side, y0, y1, floors):
    walls = kit.wall_builder(idx)
    x_in, x_out = (-XF - DEPTH, -XF) if side < 0 else (XF, XF + DEPTH)
    poly = [(x_in, y0), (x_out, y0), (x_out, y1), (x_in, y1)]              # CCW
    street = 1 if side < 0 else 3                                          # index of the street wall
    levels = asm.stack(0.0, [GROUND_H] + [FLOOR_H] * floors)
    colour = rng.choice(list(NEON))
    n_cols = max(1, int((y1 - y0 - 1.0) / 2.3))
    o_shop = o_door = None
    sign_level = 2 if floors >= 3 and rng.random() < 0.7 else None         # storey that carries a wall sign
    for li, (z0, z1) in enumerate(levels):
        def openings(i, f, li=li):
            if i != street:
                return None
            if li == 0:
                cols = f.columns([0.7, None, 0.55, 1.25, 0.7])
                return [cols[1].inset(0, 0, 0.55, f.h - 3.25, f"B{idx} shop"),
                        cols[3].inset(0, 0, 0.0, f.h - 2.45, f"B{idx} door")]
            return f.inset(0.7, 0.7, 0.95, 0.6).grid(n_cols, 1, gap=0.75)

        frames, ops = arch.shell(walls, poly, z0, z1 - z0, WALL_T, openings, name=f"B{idx} L{li} wall")
        sf = frames[street]
        if li == 0:
            o_shop, o_door = ops[street]
            shopfront(kit, o_shop, o_door, colour, rng.choice(WORDS))
        else:
            for o in ops[street]:
                upper_window(kit, sf, o, ac=li != sign_level)
            if li == sign_level:
                wall_sign(kit, sf, rng.choice(list(NEON)), rng.choice(WORDS))
        block(kit.metal, sf.sub(0, sf.h - 0.2, sf.w, 0.2, f"B{idx} L{li} cornice"), out=0.14)

    top = levels[-1][1]
    face = Frame.wall(poly[street], poly[(street + 1) % 4], 0.0, top, f"B{idx} face")   # whole street facade
    walls.tag = f"B{idx} roof"
    walls.prism(poly, top, top + 0.25)
    roof = Frame.of_box(((x_in + x_out) / 2, (y0 + y1) / 2, top + 0.125), (DEPTH, y1 - y0, 0.25), "+Z", f"B{idx} roof")
    edge = roof.sub(roof.w - 0.3 if side < 0 else 0.0, 0, 0.3, roof.h)     # parapet along the street edge
    block(walls, edge, out=0.8, label=f"B{idx} parapet")
    for k in range(rng.randint(1, 3)):                                     # roof plant
        spot = roof.centered(1.4, 1.1, s=rng.uniform(2.5, DEPTH - 2.5), t=rng.uniform(1.2, roof.h - 1.2))
        unit = block(kit.metal, spot, out=rng.uniform(0.7, 1.3), label=f"B{idx} roof unit")
        kit.pipes.tag = f"B{idx} mast"
        kit.pipes.cyl(unit.front.center, unit.front.at(unit.front.w / 2, unit.front.h / 2, rng.uniform(1.5, 4.0)), 0.03)

    if rng.random() < 0.55:
        canopy(kit, face, face.locate(o_shop.frame.origin)[0] - 0.3, o_shop.frame.w + 0.6, colour)
    if floors >= 2 and rng.random() < 0.8:
        word = rng.choice([w for w in WORDS if len(w) <= 7])
        blade_sign(kit, face, 0.35, GROUND_H + 0.6, min(top - GROUND_H - 1.0, 0.75 * len(word) + 0.6),
                   rng.choice(list(NEON)), word)
    rod(kit.pipes, face, face.w - 0.3, 0.0, face.w - 0.3, top - 0.1, 0.055, label=f"B{idx} downpipe")
    for t in range(1, int(top), 3):                                        # pipe clamps
        block(kit.metal, face.centered(0.2, 0.06, s=face.w - 0.3, t=t + 0.5), out=0.1, label=f"B{idx} clamp")
    return face


def build_blocks(kit):
    faces = {-1: [], 1: []}
    idx = 0
    for side in (-1, 1):
        y = -6.0
        while y < LENGTH - 8:
            width = rng.choice([7.0, 8.5, 10.0, 12.0])
            floors = rng.randint(2, 5) if y < 55 else rng.randint(4, 8)
            faces[side].append(building(kit, idx, side, y, y + width, floors))
            y += width                                                     # next lot starts where this one ends
            idx += 1
    return faces


def cables(faces):
    mat = dk.simple("Cable", (0.01, 0.01, 0.01), rough=0.5)
    for k in range(7):
        a, b = rng.choice(faces[-1][:6]), rng.choice(faces[1][:6])
        ta, tb = rng.uniform(5, a.h - 0.5), rng.uniform(5, b.h - 0.5)
        # anchor on the solid end piers (0.7 m wide), never over a window opening
        dk.cable(a.at(rng.choice((0.3, a.w - 0.3)), ta, -0.03), b.at(rng.choice((0.3, b.w - 0.3)), tb, -0.03),
                 sag=rng.uniform(0.06, 0.14), radius=0.018, material=mat, name=f"Cable{k}")


def van(kit, ground, x0y0, x, y):
    """Original boxy utility van parked at the kerb; wheels rest on the road."""
    x0, y0 = x0y0
    base = ground.sub(x0 + x - 0.95, y0 + y - 2.3, 1.9, 4.6, "van")
    paint = dk.MeshBuilder("VanPaint", dk.simple("VanPaint", (0.02, 0.03, 0.05), rough=0.3, metal=0.5), bevel=0.02)
    body = block(paint, base.inset(0.06, 0.06, 0.0, 0.0), out=1.05, back=-0.28, label="van body")
    cab = block(paint, body.front.inset(0.05, 0.05, 1.0, 0.25), out=0.75, back=0.02, label="van box")
    block(kit.glass, body.front.inset(0.12, 0.12, 0.12, 3.75), out=0.5, back=0.02, label="van windscreen")
    for t in (0.85, base.h - 0.85):
        for s0, s1 in ((0.0, 0.3), (base.w - 0.3, base.w)):
            kit.pipes.tag = "van wheel"
            kit.pipes.cyl(base.at(s0, t, 0.33), base.at(s1, t, 0.33), 0.34, segments=24)
    block(kit.neon["red"], body.top.inset(0.15, 0.15, 0.3, 0.35), out=0.02, label="van tail light")
    block(kit.neon["cyan"], body.bottom.inset(0.2, 0.2, 0.3, 0.35), out=0.02, label="van head light")
    for sidef in (body.left, body.right):
        block(kit.neon["cyan"], sidef.inset(0.35, 0.34, 0.2, 0.2), out=0.012, label="van side strip")
    block(kit.neon["amber"], cab.front.centered(1.0, 0.18, t=0.25), out=0.1, label="van roof bar")
    paint.finish()


def skyline(end_y):
    city = dk.MeshBuilder("Skyline", dk.city_windows(lit=0.88, strength=1.6))
    for k in range(26):
        w, h = rng.uniform(16, 34), rng.uniform(60, 190)
        x = rng.uniform(-150, 150)
        y = end_y + rng.uniform(25, 190)
        city.tag = f"tower {k}"
        city.box((x, y, h / 2 - 0.05), (w, w * rng.uniform(0.7, 1.3), h))
    city.finish()
    # building closing the street, with a big screen attached to its facade
    close = dk.MeshBuilder("EndBlock", dk.concrete("EndBlock", tone=(0.03, 0.05)))
    close.tag = "end block"
    close.box((0, end_y + 9, 19.95), (44, 14, 40))
    close.finish()
    face = Frame.of_box((0, end_y + 9, 20), (44, 14, 40), "-Y", "end block face")
    screen_mat = dk.interior("BigScreen", (0.9, 0.15, 0.6), 5.0)
    screen = dk.MeshBuilder("BigScreen", screen_mat)
    frame = dk.MeshBuilder("ScreenFrame", dk.simple("ScreenFrame", (0.02, 0.02, 0.02), rough=0.3, metal=0.8), bevel=0.02)
    housing = block(frame, face.centered(15, 9, t=17, name="screen housing"), out=0.5)
    block(screen, housing.front.inset(0.4), out=0.03, label="screen")
    screen.finish()
    frame.finish()


def rain(count=450):
    me = bpy.data.meshes.new("RainDrop")
    bm = bmesh.new()
    bmesh.ops.create_cone(bm, cap_ends=False, segments=4, radius1=0.003, radius2=0.003, depth=0.55)
    bm.to_mesh(me)
    bm.free()
    me.materials.append(dk.emissive("RainGlow", (0.6, 0.75, 1.0), 0.3))
    for i in range(count):
        d = bpy.data.objects.new(f"Rain.{i:03d}", me)                      # meant to float: audit(allow="Rain*")
        bpy.context.scene.collection.objects.link(d)
        d.location = (rng.uniform(-9, 9), rng.uniform(3, 45), rng.uniform(0.3, 9))
        d.rotation_euler = (math.radians(rng.uniform(-3, 3)), math.radians(7), 0)
        d.visible_shadow = False


# ------------------------------------------------------------- look
def lighting():
    lp.set_world_color((0.012, 0.025, 0.05), 0.3)
    for name, loc, col, power in (("SpillPink", (-6.5, 14, 3.5), NEON["pink"], 350),
                                  ("SpillCyan", (6.5, 24, 3.5), NEON["cyan"], 350),
                                  ("SpillAmber", (-6.5, 40, 4.0), NEON["amber"], 250),
                                  ("SpillViolet", (6.0, 8, 5.0), NEON["violet"], 200)):
        light = bpy.data.lights.new(name, "AREA")
        light.energy, light.size, light.color = power, 3.0, col
        o = new_object(name, light)
        o.location = loc
        look_at(o, (0, loc[1] + 2, 0))
    lp.set_view("AgX", "AgX - Medium High Contrast")


def camera():
    data = bpy.data.cameras.new("Camera")
    data.lens, data.clip_end = 22, 600
    cam = new_object("Camera", data)
    if arg_value("--shot", "street") == "closeup":                         # oblique look at one facade
        data.lens = 32
        cam.location = (-1.5, 2.0, 2.4)
        look_at(cam, (-7.7, 11.0, 4.6))
    else:
        cam.location = (-2.6, -3.5, 1.65)                                  # standing on the road, eye level
        look_at(cam, (1.2, 40, 6.5))
    bpy.context.scene.camera = cam


def compositor():
    scene = bpy.context.scene
    bpy.context.view_layer.use_pass_mist = True
    scene.world.mist_settings.start, scene.world.mist_settings.depth = 8, 110
    scene.world.mist_settings.falloff = "QUADRATIC"
    tree = bpy.data.node_groups.new("Compositor", "CompositorNodeTree")
    tree.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    scene.compositing_node_group = tree
    rl = tree.nodes.new("CompositorNodeRLayers")
    amount = tree.nodes.new("ShaderNodeMath")
    amount.operation, amount.inputs[1].default_value = "MULTIPLY", 0.5
    mix = tree.nodes.new("ShaderNodeMix")
    mix.data_type = "RGBA"
    mix.inputs["B"].default_value = (0.03, 0.09, 0.13, 1)                  # teal haze
    glare = tree.nodes.new("CompositorNodeGlare")
    glare.inputs["Type"].default_value = "Bloom"
    glare.inputs["Threshold"].default_value = 1.0
    glare.inputs["Strength"].default_value = 0.55
    glare.inputs["Size"].default_value = 0.6
    out = tree.nodes.new("NodeGroupOutput")
    link = tree.links.new
    link(rl.outputs["Mist"], amount.inputs[0])
    link(amount.outputs[0], mix.inputs["Factor"])
    link(rl.outputs["Image"], mix.inputs["A"])
    link(mix.outputs["Result"], glare.inputs["Image"])
    link(glare.outputs["Image"], out.inputs["Image"])


if __name__ == "__main__":
    out_path = arg_value("--out", "/tmp/neon_street.png")
    rx, ry = (int(v) for v in arg_value("--res", "960x540").split("x"))
    clean_scene()
    setup_render("CYCLES", samples=int(arg_value("--samples", "32")), res=(rx, ry))
    bpy.context.scene.cycles.sample_clamp_indirect = 8.0

    kit = Kit()
    ground, walks, origin = build_ground(kit)
    faces = build_blocks(kit)
    for side in (-1, 1):
        props(kit, walks[side], side)
    van(kit, ground, origin, 2.9, 11.0)
    kit.finish()
    cables(faces)
    skyline(LENGTH + 6)

    report = asm.audit()                    # before the rain, which floats on purpose
    if not report["ok"]:
        raise SystemExit("assembly audit failed: fix the detached / z-fighting parts listed above")
    rain()
    lighting()
    camera()
    compositor()
    if arg_value("--blend"):
        bpy.ops.wm.save_as_mainfile(filepath=arg_value("--blend"))
    if "--no-render" not in get_args():
        render_still(out_path)
