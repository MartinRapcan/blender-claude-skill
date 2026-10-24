"""
Check that every lighting preset still hits its calibrated exposure, and that
results do not depend on scene scale.

    python tests/test_lighting_presets.py          # pip `bpy` module
    blender -b --factory-startup --python-exit-code 1 -P tests/test_lighting_presets.py

Test scene: clay monkey, chrome sphere, orange cube on a grey floor (the scene
the presets were calibrated on). Exit code 1 on any failure.
"""

import math
import os
import sys

import bpy  # before bmesh with the pip module
import bmesh

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "blender", "scripts"))
import lighting_presets as lp  # noqa: E402

# preset -> calibrated mean display luminance (AgX, exposure 0)
TARGETS = {
    "studio": 0.42, "high_key": 0.63, "low_key": 0.25, "outdoor": 0.46,
    "golden_hour": 0.35, "overcast": 0.46, "night_neon": 0.19,
}
TOLERANCE = 0.06


def build(scale):
    for o in list(bpy.data.objects):
        bpy.data.objects.remove(o, do_unlink=True)
    bpy.data.orphans_purge(do_recursive=True)

    def material(name, col, metal=0.0, rough=0.4):
        m = bpy.data.materials.new(name)
        b = m.node_tree.nodes["Principled BSDF"]
        b.inputs["Base Color"].default_value = (*col, 1)
        b.inputs["Metallic"].default_value = metal
        b.inputs["Roughness"].default_value = rough
        return m

    def add(name, fn, loc, mat, smooth=True):
        me = bpy.data.meshes.new(name)
        bm = bmesh.new()
        fn(bm)
        bm.to_mesh(me)
        bm.free()
        for p in me.polygons:
            p.use_smooth = smooth
        o = bpy.data.objects.new(name, me)
        bpy.context.scene.collection.objects.link(o)
        o.location = [v * scale for v in loc]
        o.scale = (scale,) * 3
        me.materials.append(mat)
        return o

    monkey = add("Monkey", lambda bm: bmesh.ops.create_monkey(bm), (0, 0, 1.0),
                 material("clay", (0.8, 0.75, 0.7), rough=0.5))
    monkey.rotation_euler = (math.radians(-10), 0, math.radians(15))
    monkey.modifiers.new("sub", "SUBSURF").levels = 2
    add("Sphere", lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=48, v_segments=24, radius=0.6),
        (1.7, 0.4, 0.6), material("chrome", (0.9, 0.9, 0.92), 1.0, 0.15))
    cube = add("Cube", lambda bm: bmesh.ops.create_cube(bm, size=1.0), (-1.6, 0.3, 0.5),
               material("orange", (0.9, 0.3, 0.05)), smooth=False)
    cube.rotation_euler.z = math.radians(30)
    cube.modifiers.new("bev", "BEVEL").width = 0.04 * scale
    add("Ground", lambda bm: bmesh.ops.create_grid(bm, x_segments=1, y_segments=1, size=30),
        (0, 0, 0), material("floor", (0.5, 0.5, 0.5), rough=0.7))

    scene = bpy.context.scene
    scene.render.engine = "CYCLES"
    scene.cycles.samples = 16
    scene.cycles.use_denoising = True
    scene.render.resolution_x, scene.render.resolution_y = 640, 360
    cd = bpy.data.cameras.new("Cam")
    cd.lens = 40
    cd.clip_start, cd.clip_end = 0.01 * scale, 500 * scale
    cam = bpy.data.objects.new("Cam", cd)
    scene.collection.objects.link(cam)
    cam.location = (1.2 * scale, -6.5 * scale, 2.6 * scale)
    lp.aim(cam, (0, 0, 0.7 * scale))
    scene.camera = cam


def main():
    failures = []
    cases = [(name, 1.0) for name in TARGETS] + [("studio", 0.02), ("studio", 20.0)]
    for name, scale in cases:
        build(scale)
        lp.apply_preset(name)
        lum = lp.measure_luminance()
        ok = abs(lum - TARGETS[name]) <= TOLERANCE
        print(f"  {'ok  ' if ok else 'FAIL'}  {name:12s} scale={scale:<5} lum={lum:.3f} "
              f"(target {TARGETS[name]:.2f})")
        if not ok:
            failures.append(name)
    print(f"\n{len(cases) - len(failures)}/{len(cases)} preset checks passed, "
          f"Blender {bpy.app.version_string}")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
