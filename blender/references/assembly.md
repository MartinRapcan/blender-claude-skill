# Assembly: parts that actually meet

Read this for **anything made of more than a few parts**: buildings, streets,
interiors, vehicles, machines, furniture. It fixes the most common failure of
generated scenes: parts built "layer after layer" with hand-typed coordinates,
so windows float in front of walls, signs hang in the air, pieces miss each
other by a few centimetres or overlap in the same plane, and sizes drift.

Two habits remove the whole class of error:

1. **Build by attachment** (`scripts/assembly.py`, any subject; plus
   `scripts/architecture.py` for walls, openings and windows): a child part is placed in
   the coordinates of a **Frame** taken from its parent, never in typed world
   coordinates, and every builder call returns the frames of what it built.
2. **Audit before rendering**: `assembly.audit()` reports every part that
   touches nothing, every visible coplanar overlap and every inside-out part,
   with the gap and the move that closes it. Zero findings is a condition for
   rendering, like a test suite.

## Contents
1. The rules
2. Frames: the only place coordinates come from
3. block(): attach a part, get its faces back
4. Walls with real openings; windows, doors, rooms
5. Layout helpers
6. audit(): what it reports and how to fix it
7. Worked example and pitfalls

---

## 1. The rules

- **One layout, written once.** Put the module sizes in constants at the top
  (storey height, bay width, wall thickness, road width). Everything else is
  derived with `stack`, `columns`, `rows`, `grid`, `inset`, `centered`.
  Regular spacing comes from the layout functions, not from typing 1.2, 2.4, 3.7.
- **Children live in the parent's frame.** Need a window? Ask the wall for an
  opening. Need a sign above the window? `opening.frame.rel(...)`. Need a light
  under the canopy? `canopy.bottom`. If you are about to type an absolute
  `(x, y, z)` for anything but the first root object, stop and find the frame.
- **Embed, don't touch.** An attached part sinks 1–2 cm into its parent
  (`block()` does this by default). Parts that merely touch leave hairline
  gaps or flicker; parts that overlap slightly are solid.
- **Holes belong to the wall.** A wall is built with its openings
  (`wall`/`shell`); the window is then fitted **into the Opening it returns**.
  Never build a wall and separately place a window "at the same position".
- **Each layer queries the previous one.** Builders return `Block`s, `Opening`s
  and `Frame`s. Keep them and build on them instead of recomputing positions.
- **Fail loudly.** `Frame.sub()` raises if a part does not fit its parent,
  `wall()` raises on overlapping openings. A raised error is a layout bug found
  early; never work around it by nudging numbers.
- **Audit, fix, then render.** A clean audit means the scene is one connected
  assembly standing on the ground.

---

## 2. Frames: the only place coordinates come from

A `Frame` is a rectangle in space with its own axes: `s` along it, `t` up it,
`d` out of it (along the outward normal). `frame.at(s, t, d)` is the only
conversion to world space you need.

```python
import sys
sys.path.insert(0, "/path/to/skill/blender/scripts")
import bpy
import assembly as asm
from assembly import Frame

front = Frame.wall((0, 0), (8, 0), z0=0, height=3.2, name="front")   # a -> b, CCW around the footprint
print(front)                       # origin, size and normal: here n = (0, -1, 0)
print(front.at(4, 1.6, 0.5))       # 4 m along, 1.6 m up, 0.5 m in front of the wall

box_top = Frame.of_box(center=(0, 0, 1), size=(2, 1, 2), side="+Z")  # a side of any box
obj = bpy.data.objects.new("Probe", bpy.data.meshes.new("Probe"))
obj.data.from_pydata([(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 1)], [], [(0, 1, 2, 3)])
bpy.context.scene.collection.objects.link(obj)
side = Frame.of_object(obj, "-Y")                                    # a side of an existing object
bpy.data.objects.remove(obj)

inner = front.inset(0.6, 0.6, 0.9, 0.5)          # margins: left, right, bottom, top
cells = inner.grid(cols=3, rows=1, gap=0.7)      # three equal, evenly spaced cells
door = front.centered(1.0, 2.1, t=1.05)          # centred horizontally, standing on the floor
above = cells[0].rel(0, cells[0].h + 0.2, h=0.5) # same plane, just above the first cell
print(front.locate((2, 0, 1)))                   # world point -> (s, t, d) in this frame
```

`sub()`, `centered()`, `inset()`, `grid()`, `columns()`, `rows()` return frames
**inside** the parent and raise if they would stick out. `rel()` and `offset()`
make neighbours in or parallel to the same plane.

---

## 3. block(): attach a part, get its faces back

`block(builder, frame, out)` makes a box that covers `frame` exactly, extends
`out` metres along its normal and sinks `back` (2 cm) into the parent. It
returns a `Block` with a Frame on every face: `front` (parallel to the parent
frame; for a block standing on a floor frame that is its top), `left`, `right`,
`top`, `bottom`, `back`. Chain them:

```python
import assembly as asm
import detail_kit as dk
from assembly import Frame, block, rod, outline, text_on

metal = dk.MeshBuilder("Metal", dk.panel_metal(), bevel=0.005)
light = dk.MeshBuilder("Lights", dk.emissive("Strip", (0.9, 0.95, 1.0), 12))
wallb = dk.MeshBuilder("Wall", dk.concrete())
front = Frame.wall((0, 0), (8, 0), 0, 4.0, "front")
block(wallb, front, out=0.0, back=0.3, label="wall slab")              # the parent

canopy = block(metal, front.sub(1, 2.8, 6, 0.15), out=1.4, label="canopy")   # on the wall
block(light, canopy.bottom.inset(0.1, 0.1, 1.0, 0.2), out=0.03, label="canopy light")  # under it
board = block(metal, canopy.top.centered(3.0, 0.2, t=0.2), out=0.7, label="sign board")  # standing on it
print(board.bottom)                                                    # the face that looks at the street
text_on(board.bottom.inset(0.05), "OPEN", dk.emissive("Neon", (1, 0.1, 0.5), 20))
rod(metal, front, 7.6, 0.0, 7.6, 4.0, 0.05, label="downpipe")          # a pipe ON the wall
outline(light, front.sub(1.5, 0.4, 2.0, 2.0), 0.02)                    # neon border around an area
for b in (metal, light, wallb):
    b.finish()
report = asm.audit()
assert report["ok"]
```

`MeshBuilder.tag` / the `label=` arguments name parts; the audit uses those
names. Other attachers: `rod` (pipe over a frame), `outline` (four rods around
a frame), `text_on` (extruded text scaled to fit a frame).

On a block that stands on a **floor** frame, the side frames have one axis
pointing up: print the frame (`print(blk.left)`) when unsure which is which.

---

## 4. Walls with real openings; windows, doors, rooms

`scripts/architecture.py` applies the same rules to buildings (assembly.py
itself knows nothing about walls). The principle carries over to any object
with holes: **the hole belongs to the parent, the filling is fitted into the
hole it returns.**

```python
import architecture as arch
import assembly as asm
import detail_kit as dk
from architecture import window, room, plug
from assembly import block

walls = dk.MeshBuilder("Walls", dk.concrete())
frames = dk.MeshBuilder("Frames", dk.simple("FrameMetal", (0.04, 0.04, 0.045), metal=0.8), bevel=0.004)
glass = dk.MeshBuilder("Glass", dk.glass())
rooms = dk.MeshBuilder("Rooms", dk.interior("Room", (1.0, 0.65, 0.3), 2.5))
doors = dk.MeshBuilder("Doors", dk.wood())
ground = dk.MeshBuilder("Ground", dk.concrete("GroundMat"))
ground.box((4, 3, -0.1), (30, 30, 0.2))

footprint = [(0, 0), (8, 0), (8, 6), (0, 6)]                 # CCW, any angles
for level, (z0, z1) in enumerate(asm.stack(0.0, [3.6, 3.0, 3.0])):   # storeys meet exactly
    def openings(i, f, level=level):
        if i != 0:                                           # only the front wall has openings here
            return None
        if level == 0:
            cols = f.columns([0.6, None, 0.5, 1.1, 0.6])     # pier, shop window, pier, door, pier
            return [cols[1].inset(0, 0, 0.5, 0.6, "shop"), cols[3].inset(0, 0, 0.0, 1.3, "door")]
        return f.inset(0.7, 0.7, 0.9, 0.5).grid(cols=3, rows=1, gap=0.7)

    wall_frames, per_wall = arch.shell(walls, footprint, z0, z1 - z0, 0.3, openings, name=f"L{level} wall")
    for o in per_wall[0]:
        if o.name == "door":
            leaf = plug(doors, o, setback=0.1, thickness=0.05)
            block(frames, leaf.centered(0.04, 0.25, s=leaf.w * 0.85, t=1.05), out=0.05, label="handle")
        else:
            window(frames, glass, o, cols=3 if o.name == "shop" else 2)   # fills the hole exactly
            room(rooms, o, depth=1.2)                                     # what you see through the glass
for b in (walls, frames, glass, rooms, doors, ground):
    b.finish()
assert asm.audit()["ok"]
```

- `wall(builder, frame, thickness, openings)` builds **one watertight island**
  with outer face, inner face and the reveals of every hole. `frame` is the
  outer surface; the wall extends `thickness` behind it.
- `shell(builder, polygon, z0, height, thickness, openings)` builds all walls
  of a storey with mitred corners, so they meet without overlapping at any
  angle. `openings(i, frame)` returns sub-frames for wall `i`.
- An `Opening` has `.frame` (the hole on the outer surface), `.depth`, `.back`,
  `.sill` and `.head` (reveal frames for blinds, lights, planters).
- `room()` labels its part `[hidden]`: the audit does not report hidden
  parts sharing a plane. Use the same tag for any geometry nobody can see.
- `window()` puts frame members on all four edges of the hole (sunk into the
  reveals), mullions, one glass sheet caught in the members and a sill.
- `architecture.FacadeKit` is built on these: `facade_segment` and `panelize`
  produce cladding panels, recessed windows, vents and fins that pass the audit.

---

## 5. Layout helpers

| Helper | Use |
|---|---|
| `asm.stack(z0, [h1, h2, ...])` | storeys/layers that start exactly where the last one ends |
| `frame.columns(spec, gap, margin)` / `rows(...)` | `spec` = count, or widths with `None` = "share the rest" |
| `frame.grid(cols, rows, gap, margin)` | regular cells, each with `.col`, `.row` |
| `frame.inset(...)`, `centered(w, h, s, t)`, `sub(s, t, w, h)` | parts inside a parent (bounds-checked) |
| `frame.rel(ds, dt, w, h)`, `offset(d)`, `flipped()` | neighbours: beside/above/below, in front/behind, other side |
| `asm.footprint_walls(polygon, z0, h)` | one frame per side of any prism (footprint, casing, tank) |
| `asm.offset_polygon(polygon, d)` | slabs, plinths, cornices that follow a footprint |
| `Frame.of_object(obj, side)` | attach to something that already exists (imported model, primitive) |

Adjacent things share a boundary by construction: build lots along a street
with a running position (`y += width`), never with independently typed starts.

---

## 6. audit(): what it reports and how to fix it

```python
import assembly as asm

report = asm.audit(allow=("Rain*", "Drone*"))   # names/labels that float on purpose
print(report["ok"], report["parts"], report["groups"])
for d in report["detached"]:
    print(d["label"], d["gap"], d["nearest"], d["move_to_touch"])
```

Every mesh island is a part (text and curve objects count as one part each).
Parts in contact (overlapping, or within `tol` = 5 mm) are linked; the audit
walks that graph from the anchor objects (`Ground*`, `Road*`, `Floor*`,
`Terrain*`, `Street*`; if none exist, the largest group).

| Report line | Meaning | Fix |
|---|---|---|
| `DETACHED 'x' ... gap 0.35 m to 'y', move by (0, 0.35, 0)` | the part (or a whole group: a sign with its bracket) touches nothing connected to the ground | It was placed with its own numbers. Rebuild it on the parent's frame; do not just add the offset |
| `DETACHED ... bounding boxes overlap 'y' but surfaces never meet` | the part sits in an opening, a recess or a gap of `y` | It hangs in a hole (e.g. a cable ending in a window) or beside its parent: attach to solid surface |
| `Z-FIGHT 'a' / 'b': 0.4 m2 coplanar` | two visible faces in the same plane: flickering or striped render | Give one a different depth (`out=`) or stop one before the other starts |
| `INVERTED normals` | a closed part is inside-out (mirrored matrix, wrong vertex order) | Flip faces; `MeshBuilder.box` already corrects mirrored bases |

Options: `allow=` (intentional floaters), `whole=` (object globs treated as a
single part, e.g. imported models), `anchors=`, `tol=`, `min_area=`.
The audit reads base meshes (bevel/subdivision modifiers are ignored), so it
is fast: about 20 s for 6,000 parts.

Make it part of the script, before the render:

```python
import assembly as asm

report = asm.audit()
if not report["ok"]:
    raise SystemExit("assembly audit failed: fix the parts listed above")
```

---

## 7. Worked example and pitfalls

`examples/neon_street.py` builds a whole street this way: 20 buildings on
abutting lots, storeys from `stack`, shopfronts and windows in `shell`
openings, sign boards / blade signs / canopies / AC units / pipes on wall
frames, props on sidewalk frames, a van whose wheels rest on the road, cables
anchored on piers. About 6,000 parts, one contact group, clean audit.
Read it before building any multi-building scene.

Things the audit caught while writing that example (all real):

| Finding | Cause |
|---|---|
| Hundreds of blinds, window frames and room cards detached | parts placed by recomputed offsets instead of in the opening |
| Every box inside-out | wall bases `(u, n, up)` are left-handed; boxes must be flipped |
| Walls z-fighting at corners | two walls both filling the corner; use mitred `shell()` |
| Background towers detached | they stood beyond the edge of the ground object |
| Cables detached | random end points landed in window openings, not on wall |
| Sign board z-fighting with cornice | same `out=` depth and overlapping heights |
| A frame "size must be positive" error | margins typed for the wrong axis of a side frame: print the frame |
