"""
Execute every ```python block in blender/references/*.md against a real Blender
and report which ones fail. Catches API drift (renamed enums, removed
properties, deprecations) before the skill teaches broken code.

Run with Blender as a Python module (no GUI install needed):

    python3.13 -m venv .venv && .venv/bin/pip install bpy==5.2.3
    .venv/bin/python tests/run_snippets.py

    (Windows PowerShell: py -3.13 -m venv .venv, then .venv/Scripts/python
    instead of .venv/bin/python)

or inside Blender:

    blender -b --factory-startup --python-exit-code 1 -P tests/run_snippets.py

Each block runs in a fresh scene with fixtures (obj, mat, cam, …) so snippets can
reference the usual names. Placeholder paths like "/path/x.png" are replaced by
generated test files. Exit code 1 if any block fails.
"""

import math
import os
import re
import sys
import tempfile
import textwrap
import traceback
import warnings

import bpy  # must come before bmesh / mathutils with the pip module
import bmesh
import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = os.path.join(ROOT, "blender")
sys.path.insert(0, os.path.join(SKILL, "scripts"))
TMP = tempfile.mkdtemp(prefix="blender_skill_test_")

# Blocks that cannot run headless/in isolation; key = (file, block number).
SKIP = {
    ("animation.md", 8): "renders a whole animation (slow); single-frame rendering is covered",
}


def make_image(name, hdr=False):
    path = os.path.join(TMP, name)
    img = bpy.data.images.new(name, 32, 16, float_buffer=hdr)
    px = np.ones((16, 32, 4), np.float32) * 0.5
    img.pixels.foreach_set(px.ravel())
    img.filepath_raw = path
    img.file_format = "OPEN_EXR" if hdr else "PNG"
    img.save()
    bpy.data.images.remove(img)
    return path


def make_svg():
    path = os.path.join(TMP, "logo.svg")
    with open(path, "w") as f:
        f.write('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
                '<path d="M1 1 L9 1 L5 9 Z"/></svg>')
    return path


PNG = make_image("tex.png")
EXR = make_image("env.exr", hdr=True)
SVG = make_svg()


def mesh_obj(name, build, loc=(0, 0, 0)):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(me)
    bm.free()
    o = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(o)
    o.location = loc
    return o


def fresh_namespace():
    """Empty scene + the names snippets commonly assume exist."""
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    for c in list(bpy.data.collections):
        bpy.data.collections.remove(c)
    bpy.data.orphans_purge(do_recursive=True)
    vl = bpy.context.view_layer
    vl.active_layer_collection = vl.layer_collection
    if bpy.context.object and bpy.context.object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode="OBJECT")
    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 2
    scene.render.resolution_x, scene.render.resolution_y = 64, 36

    cube = lambda bm: bmesh.ops.create_cube(bm, size=1.0)
    obj = mesh_obj("Cube", cube)
    objA = mesh_obj("A", cube)
    objB = mesh_obj("B", cube, (0.5, 0.3, 0.2))
    mesh_obj("C", cube, (1.5, 0, 0))
    target = mesh_obj("Target", cube, (3, 0, 0))
    sources = [mesh_obj("Src1", cube, (5, 0, 0)), mesh_obj("Src2", cube, (6, 0, 0))]
    hero = mesh_obj("Hero", cube, (0, 3, 0))
    base = mesh_obj("Base", cube, (0, -3, 0))
    parent = mesh_obj("Parent", cube, (-3, 0, 0))
    child = mesh_obj("Child", cube, (-3, 0, 2))
    car = bpy.data.objects.new("Car", None)
    scene.collection.objects.link(car)
    wheel = mesh_obj("Wheel", lambda bm: bmesh.ops.create_cone(
        bm, cap_ends=True, segments=16, radius1=0.35, radius2=0.35, depth=0.2))
    mesh_obj_ = mesh_obj("Body", lambda bm: bmesh.ops.create_cube(bm, size=1.0), (0, 0, 1))
    subject = hero

    curve = bpy.data.curves.new("Path", "CURVE")
    sp = curve.splines.new("POLY")
    sp.points.add(1)
    sp.points[1].co = (5, 0, 0, 1)
    curve_obj = bpy.data.objects.new("PathObj", curve)
    scene.collection.objects.link(curve_obj)
    profile_obj = curve_obj

    mat = bpy.data.materials.new("Mat")
    obj.data.materials.append(mat)
    mat_red, mat_blue = bpy.data.materials.new("Red"), bpy.data.materials.new("Blue")
    bsdf = mat.node_tree.nodes["Principled BSDF"]

    cam_data = bpy.data.cameras.new("Cam")
    cam = bpy.data.objects.new("Cam", cam_data)
    scene.collection.objects.link(cam)
    cam.location = (0, -6, 2)
    scene.camera = cam

    key = bpy.data.objects.new("Key", bpy.data.lights.new("Key", "AREA"))
    scene.collection.objects.link(key)
    card = key

    import lighting_presets as lp  # noqa: F401  (snippets import it themselves too)
    return dict(
        bpy=bpy, bmesh=bmesh, math=math, scene=scene,
        obj=obj, objA=objA, objB=objB, target=target, sources=sources, hero=hero,
        base=base, parent=parent, child=child, car=car, wheel=wheel,
        mesh_obj=mesh_obj_, subject=subject, curve_obj=curve_obj,
        profile_obj=profile_obj, mat=mat, mat_red=mat_red, mat_blue=mat_blue,
        bsdf=bsdf, nt=mat.node_tree, links=mat.node_tree.links, cam=cam,
        cam_data=cam_data, key=key, card=card, x=1.0, y=2.0, z=0.0,
    )


def prepare(code):
    """Dedent (blocks inside list items) and swap placeholder paths for test files."""
    # Forward slashes: Windows backslashes ("C:\Users") would be read as escapes
    # inside the snippet's string literals and in re.sub replacements.
    def fwd(path):
        return path.replace("\\", "/")

    code = textwrap.dedent(code)
    code = code.replace("/path/to/skill/blender/scripts", fwd(os.path.join(SKILL, "scripts")))
    code = re.sub(r'"/path/[^"]*\.(hdr|exr)"', repr(fwd(EXR)), code)
    code = re.sub(r'"/path/[^"]*\.svg"', repr(fwd(SVG)), code)
    code = re.sub(r'"/path/[^"]*\.(png|jpg)"', repr(fwd(PNG)), code)
    code = re.sub(r'"/path/[^"]*\.ttf"', "None", code)
    code = code.replace('"/tmp/', '"' + fwd(TMP) + "/")
    return code


def run_block(code, label, ns):
    """exec a block; DeprecationWarnings raised by the snippet's own code count as
    failures, ones from inside Blender's bundled add-ons (e.g. NumPy deprecations in
    the FBX exporter) don't."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        exec(compile(prepare(code), label, "exec"), ns)
    own = [w for w in caught if issubclass(w.category, DeprecationWarning) and ".md#" in w.filename]
    if own:
        raise DeprecationWarning(f"{own[0].message} (line {own[0].lineno})")


def main():
    print("Blender", bpy.app.version_string)
    failures, total, skipped = [], 0, 0
    ref_dir = os.path.join(SKILL, "references")
    for fname in sorted(os.listdir(ref_dir)):
        if not fname.endswith(".md"):
            continue
        with open(os.path.join(ref_dir, fname), encoding="utf-8") as f:  # not the Windows locale codec
            blocks = re.findall(r"```python\n(.*?)```", f.read(), re.S)
        ns = None
        for i, code in enumerate(blocks, 1):
            if (fname, i) in SKIP:
                skipped += 1
                continue
            total += 1
            ns = fresh_namespace() if ns is None or "bpy.data.objects[" in code or i == 1 else ns
            try:
                run_block(code, f"{fname}#{i}", ns)
                print(f"  ok    {fname} #{i}")
            except Exception:  # noqa: BLE001 — report everything
                # a fresh scene may be all the block needed (it relied on fixture names)
                try:
                    ns = fresh_namespace()
                    run_block(code, f"{fname}#{i}", ns)
                    print(f"  ok    {fname} #{i} (fresh scene)")
                except Exception as e2:  # noqa: BLE001
                    failures.append((fname, i, e2))
                    print(f"  FAIL  {fname} #{i}: {type(e2).__name__}: {e2}")
                    traceback.print_exc(limit=1)
    print(f"\n{total - len(failures)}/{total} blocks passed, {skipped} skipped, "
          f"Blender {bpy.app.version_string}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
