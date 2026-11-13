"""
Attachment + audit tests for scripts/assembly.py and scripts/architecture.py.

    python tests/test_assembly.py          # pip `bpy` module
    blender -b --factory-startup --python-exit-code 1 -P tests/test_assembly.py

1. A three-storey building with mitred walls, windows in openings, rooms, a
   canopy, a sign and a downpipe must audit clean.
2. A deliberately floating part and a coplanar plate must be reported.
3. Layout mistakes (part outside its parent, overlapping openings) must raise.
4. architecture.FacadeKit output must audit clean.
"""
import os
import sys

import bpy  # before bmesh with the pip module
import bmesh  # noqa: F401

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "blender", "scripts"))
import architecture as arch  # noqa: E402
import assembly as asm  # noqa: E402
import detail_kit as dk  # noqa: E402
from architecture import room, wall, window  # noqa: E402
from assembly import Frame, audit, block, rod  # noqa: E402


def clear():
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)


clear()

walls = dk.MeshBuilder("Walls", dk.concrete())
frames = dk.MeshBuilder("Frames", dk.simple("F", (0.05,0.05,0.05)), bevel=0.004)
glass = dk.MeshBuilder("Glass", dk.glass())
rooms = dk.MeshBuilder("Rooms", dk.interior("R", (1,0.7,0.4), 3))
metal = dk.MeshBuilder("Metal", dk.simple("M", (0.3,0.3,0.3), metal=1))
ground = dk.MeshBuilder("Ground", dk.concrete("G"))
ground.box((4, 0, -0.1), (20, 20, 0.2)); ground.finish()

levels = asm.stack(0, [3.6, 3.0, 3.0])
poly = [(0,0),(8,0),(8,3),(6,5),(0,5)]
for li,(z0,z1) in enumerate(levels):
    cells = lambda i, wf: wf.inset(0.6, 0.6, 0.9, 0.5).grid(cols=3 if wf.w>6 else (2 if wf.w > 4 else 1), rows=1, gap=0.7)
    fr, ops = arch.shell(walls, poly, z0, z1-z0, 0.3, cells, f"L{li} wall")
    for o in sum(ops, []):
        window(frames, glass, o, cols=2); room(rooms, o)
    # slab band
front = Frame.wall((0,0),(8,0), 0, 3.6, "front")
can = block(metal, front.sub(1, 2.9, 6, 0.12), out=1.2, label="canopy")
block(metal, can.bottom.inset(0.1), out=0.04, label="canopy light")
rod(metal, front, 0.3, 0.0, 0.3, 3.6, 0.05, label="downpipe")
sign = block(metal, can.top.centered(3, 0.2, t=0.15), out=0.8, label="sign board")
for b in (walls, frames, glass, rooms, metal): b.finish()
r = audit()
assert r["ok"], r
# break it: floating box 0.4 m in front of the wall, and a coplanar plate
bad = dk.MeshBuilder("Bad", dk.simple("B",(1,0,0)))
bad.tag = "floating window"; bad.box(front.at(4, 1.5, 0.4), (1, 0.1, 1), front.basis)
bad.tag = "coplanar plate"; bad.box(front.at(0.48, 1.5, -0.05), (0.2, 0.1, 0.2), front.basis)
bad.finish()
r = audit()
assert not r["ok"] and len(r["detached"]) == 1 and r["detached"][0]["label"].startswith("floating window"), r
assert 0.25 < r["detached"][0]["gap"] < 0.36, r["detached"]
assert r["zfight"], "coplanar plate not detected"
for bad_call in (lambda: front.sub(7.5, 0, 1, 1, "too wide"), lambda: wall(dk.MeshBuilder("x", None), front, 0.3, [(1,1,2,2),(2,2,2,1)])):
    try: bad_call(); raise SystemExit("no error raised")
    except ValueError as e: print("raises:", str(e)[:90])

clear()
g = dk.MeshBuilder("Ground", dk.concrete())
g.box((0, 0, -0.1), (60, 60, 0.2))
g.finish()
kit = arch.FacadeKit(seed=3)
arch.facade_segment(kit, arch.octagon(12, 3), 0, 16)
arch.mech_floor(kit, arch.octagon(10, 2.5), 16, 21)
arch.facade_segment(kit, arch.rect(10, 8), 21, 37, window_ratio=0.5)
kit.finish()
r = audit()
assert r["ok"], "FacadeKit output is not a clean assembly"
print("ASSEMBLY TEST OK", bpy.app.version_string)
