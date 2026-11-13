"""
Detail kit for high-detail procedural models of ANY subject (Blender 5.x,
tested on 5.0.1 / 5.2.3). See references/detailing.md for the workflow.

General tools (machines, vehicles, props, rocks, terrain, sci-fi, buildings):
- MeshBuilder: thousands of parts merged into ONE mesh per material. Every
  part is its own island, so Geometry > "Random Per Island" in the shader gives
  each part its own tone/roughness for free.
- panel_lines(obj): real V-groove panel seams (and raised panels) on any mesh.
- greeble(target, builder): small boxes / cylinders / stacks placed on a
  surface, aligned to its normals (hard-surface "kitbash" detail).
- scatter_instances(target, source): linked duplicates spread over a surface
  (bolts, rocks, plants, debris), random scale and spin.
- rock(): displaced, applied rock mesh; cable(): sagging cable/hose/wire.
- Material recipes with variation and wear: worn_paint, panel_metal,
  concrete, wood, rock_material, glass, wet_asphalt, emissive, interior.

Attachment and the contact audit are in assembly.py; walls, windows and
facades (FacadeKit) are in architecture.py.

    import sys; sys.path.insert(0, "/path/to/skill/blender/scripts")
    import detail_kit as dk
"""

import math
import random

import bpy  # before bmesh / mathutils with the pip `bpy` module
import bmesh
from mathutils import Matrix, Vector

import assembly as asm  # frames, attachment, audit (same folder)

Z = Vector((0, 0, 1))


# =================================================================== builders
class MeshBuilder:
    """Accumulate primitives into one mesh with one material."""

    def __init__(self, name, material, bevel=0.0, collection=None):
        self.name, self.material, self.bevel = name, material, bevel
        self.collection = collection
        self.bm = bmesh.new()
        self.count = 0
        self.tag = None      # label for the parts added next (shown by assembly.audit)
        self._parts = []     # (first vertex index, label) per part

    def _mark(self):
        self._parts.append((len(self.bm.verts), self.tag))

    def box(self, center, size, basis=None):
        """Box of `size` (x, y, z) at `center`; `basis` = 3x3 rotation (e.g. Frame.basis)."""
        m = Matrix.Translation(center)
        if basis is not None:
            m = m @ basis.to_4x4()
        m = m @ Matrix.Diagonal((size[0], size[1], size[2], 1.0))
        self._mark()
        made = bmesh.ops.create_cube(self.bm, size=1.0, matrix=m)
        if m.determinant() < 0:   # mirrored basis (e.g. a wall frame) turns the box inside out
            bmesh.ops.reverse_faces(self.bm, faces=list({f for v in made["verts"] for f in v.link_faces}))
        self.count += 1

    def cyl(self, p0, p1, radius, segments=12, caps=True):
        """Cylinder between two points (pipes, rails, struts, masts)."""
        p0, p1 = Vector(p0), Vector(p1)
        d = p1 - p0
        rot = Z.rotation_difference(d.normalized()).to_matrix().to_4x4()
        self._mark()
        bmesh.ops.create_cone(self.bm, cap_ends=caps, segments=segments, radius1=radius,
                              radius2=radius, depth=d.length,
                              matrix=Matrix.Translation((p0 + p1) / 2) @ rot)
        self.count += 1

    def dot(self, center, radius):
        """Small sphere: light points, rivets, bolts."""
        self._mark()
        bmesh.ops.create_icosphere(self.bm, subdivisions=1, radius=radius,
                                   matrix=Matrix.Translation(center))
        self.count += 1

    def prism(self, polygon, z0, z1):
        """Closed vertical prism from a CCW 2D polygon (cores, slabs, plinths)."""
        self._mark()
        bot = [self.bm.verts.new((x, y, z0)) for x, y in polygon]
        top = [self.bm.verts.new((x, y, z1)) for x, y in polygon]
        self.bm.faces.new(list(reversed(bot)))
        self.bm.faces.new(top)
        for i in range(len(polygon)):
            j = (i + 1) % len(polygon)
            self.bm.faces.new((bot[i], bot[j], top[j], top[i]))
        self.count += 1

    def finish(self):
        """Write the mesh, link the object, add a small bevel. Returns the object (or None)."""
        if not self.count:
            self.bm.free()
            return None
        me = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for p in me.polygons:
            p.use_smooth = True
        if self.bevel <= 0 and hasattr(me, "set_sharp_from_angle"):
            me.set_sharp_from_angle(angle=math.radians(40))   # round parts smooth, boxes crisp
        if any(label for _, label in self._parts):
            import json
            me["dk_parts"] = json.dumps(self._parts)
        obj = bpy.data.objects.new(self.name, me)
        (self.collection or bpy.context.scene.collection).objects.link(obj)
        me.materials.append(self.material)
        if self.bevel > 0:  # catches a highlight on every edge; harden keeps faces flat
            bev = obj.modifiers.new("Bevel", "BEVEL")
            bev.width = self.bevel
            bev.segments = 1
            bev.limit_method = "ANGLE"
            bev.harden_normals = True
        return obj


# ================================================================== materials
def _principled(name):
    m = bpy.data.materials.new(name)
    return m, m.node_tree, m.node_tree.links, m.node_tree.nodes["Principled BSDF"]


def simple(name, color, rough=0.5, metal=0.0):
    m, _, _, b = _principled(name)
    b.inputs["Base Color"].default_value = (*color, 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    return m


def emissive(name, color, strength):
    """Light sources. Strength guide: tiny dots (r 5 cm) 50-80, strips 10-20,
    neon tubes 20-30, screens 4-8, room interiors 1-4 (see detailing.md)."""
    m, _, _, b = _principled(name)
    b.inputs["Base Color"].default_value = (0, 0, 0, 1)
    b.inputs["Emission Color"].default_value = (*color, 1)
    b.inputs["Emission Strength"].default_value = strength
    return m


def _grime(nt, L, coord, vertical_stretch=0.08, scale=3.0, low=0.45):
    """Factor 1 = clean, `low` = dirty; noise stretched along Z gives rain streaks."""
    stretch = nt.nodes.new("ShaderNodeMapping")
    stretch.inputs["Scale"].default_value = (1.5, 1.5, vertical_stretch)
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    noise.inputs["Detail"].default_value = 8.0
    rng = nt.nodes.new("ShaderNodeMapRange")
    rng.inputs["From Min"].default_value = 0.4
    rng.inputs["From Max"].default_value = 0.7
    rng.inputs["To Min"].default_value = 1.0
    rng.inputs["To Max"].default_value = low
    L.new(coord.outputs["Object"], stretch.inputs["Vector"])
    L.new(stretch.outputs["Vector"], noise.inputs["Vector"])
    L.new(noise.outputs["Fac"], rng.inputs["Value"])
    return rng.outputs["Result"]


def _bump(nt, L, coord, b, scale=40.0, strength=0.05):
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = scale
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = strength
    L.new(coord.outputs["Object"], noise.inputs["Vector"])
    L.new(noise.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], b.inputs["Normal"])


def _per_island(nt, L, lo, hi):
    geo = nt.nodes.new("ShaderNodeNewGeometry")
    mr = nt.nodes.new("ShaderNodeMapRange")
    mr.inputs["To Min"].default_value = lo
    mr.inputs["To Max"].default_value = hi
    L.new(geo.outputs["Random Per Island"], mr.inputs["Value"])
    return geo, mr.outputs["Result"]


def _rgb(nt, L, value_socket, tint):
    """Grey value * tint -> colour."""
    comb = nt.nodes.new("ShaderNodeCombineColor")
    for i, k in enumerate(tint):
        mul = nt.nodes.new("ShaderNodeMath")
        mul.operation = "MULTIPLY"
        mul.inputs[1].default_value = k
        L.new(value_socket, mul.inputs[0])
        L.new(mul.outputs[0], comb.inputs[i])
    return comb.outputs["Color"]


def panel_metal(name="PanelMetal", tone=(0.018, 0.06), rough=(0.3, 0.65),
                metallic=0.6, tint=(0.92, 1.0, 1.12), edge_wear=0.06):
    """Painted metal cladding: per-panel tone and roughness, rain streaks,
    lighter worn edges (Geometry > Pointiness, Cycles only), fine bump."""
    m, nt, L, b = _principled(name)
    b.inputs["Metallic"].default_value = metallic
    coord = nt.nodes.new("ShaderNodeTexCoord")
    geo, val = _per_island(nt, L, *tone)
    _, r = _per_island(nt, L, *rough)
    dirty = nt.nodes.new("ShaderNodeMath")
    dirty.operation = "MULTIPLY"
    L.new(val, dirty.inputs[0])
    L.new(_grime(nt, L, coord), dirty.inputs[1])
    edge = nt.nodes.new("ShaderNodeMapRange")
    edge.inputs["From Min"].default_value = 0.52
    edge.inputs["From Max"].default_value = 0.62
    edge.inputs["To Max"].default_value = edge_wear
    L.new(geo.outputs["Pointiness"], edge.inputs["Value"])
    add = nt.nodes.new("ShaderNodeMath")
    add.operation = "ADD"
    L.new(dirty.outputs[0], add.inputs[0])
    L.new(edge.outputs["Result"], add.inputs[1])
    L.new(_rgb(nt, L, add.outputs[0], tint), b.inputs["Base Color"])
    L.new(r, b.inputs["Roughness"])
    _bump(nt, L, coord, b)
    return m


def concrete(name="Concrete", tone=(0.25, 0.38), tint=(1.0, 0.97, 0.92)):
    """Cast concrete: per-slab tone, large stains, rain streaks, pitted bump."""
    m, nt, L, b = _principled(name)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    _, val = _per_island(nt, L, *tone)
    stains = nt.nodes.new("ShaderNodeTexNoise")
    stains.inputs["Scale"].default_value = 0.4
    st = nt.nodes.new("ShaderNodeMapRange")
    st.inputs["To Min"].default_value = 0.7
    st.inputs["To Max"].default_value = 1.05
    L.new(coord.outputs["Object"], stains.inputs["Vector"])
    L.new(stains.outputs["Fac"], st.inputs["Value"])
    a = nt.nodes.new("ShaderNodeMath")
    a.operation = "MULTIPLY"
    L.new(val, a.inputs[0])
    L.new(st.outputs["Result"], a.inputs[1])
    c = nt.nodes.new("ShaderNodeMath")
    c.operation = "MULTIPLY"
    L.new(a.outputs[0], c.inputs[0])
    L.new(_grime(nt, L, coord, low=0.55), c.inputs[1])
    L.new(_rgb(nt, L, c.outputs[0], tint), b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.85
    _bump(nt, L, coord, b, scale=120.0, strength=0.12)
    return m


def worn_paint(name="WornPaint", paint=(0.55, 0.18, 0.04), metal=(0.55, 0.55, 0.57),
               wear=0.5, tone=(0.85, 1.1)):
    """Painted metal that is chipped on edges (bare metal shows through),
    per-part tone variation, grime streaks. `wear` 0..1. Cycles (Pointiness)."""
    m, nt, L, b = _principled(name)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    geo, var = _per_island(nt, L, *tone)
    chips = nt.nodes.new("ShaderNodeTexNoise")              # breaks up the edge line
    chips.inputs["Scale"].default_value = 25.0
    chips.inputs["Detail"].default_value = 6.0
    L.new(coord.outputs["Object"], chips.inputs["Vector"])
    edge = nt.nodes.new("ShaderNodeMath")
    edge.operation = "ADD"
    L.new(geo.outputs["Pointiness"], edge.inputs[0])
    shift = nt.nodes.new("ShaderNodeMath")
    shift.operation = "MULTIPLY"
    shift.inputs[1].default_value = 0.08
    L.new(chips.outputs["Fac"], shift.inputs[0])
    L.new(shift.outputs[0], edge.inputs[1])
    # pointiness is ~0.5 on flat areas; only clearly convex edges (plus noise)
    # cross the threshold. More wear lowers it.
    mask = nt.nodes.new("ShaderNodeMapRange")
    mask.inputs["From Min"].default_value = 0.635 - 0.06 * wear
    mask.inputs["From Max"].default_value = 0.645 - 0.06 * wear
    L.new(edge.outputs[0], mask.inputs["Value"])
    paint_col = nt.nodes.new("ShaderNodeMix")
    paint_col.data_type = "RGBA"
    paint_col.blend_type = "MULTIPLY"
    paint_col.inputs["Factor"].default_value = 1.0
    paint_col.inputs["A"].default_value = (*paint, 1)
    tint = nt.nodes.new("ShaderNodeCombineColor")
    for i in range(3):
        L.new(var, tint.inputs[i])
    L.new(tint.outputs["Color"], paint_col.inputs["B"])
    dirty = nt.nodes.new("ShaderNodeMix")
    dirty.data_type = "RGBA"
    dirty.blend_type = "MULTIPLY"
    dirty.inputs["Factor"].default_value = 1.0
    L.new(paint_col.outputs["Result"], dirty.inputs["A"])
    grime = nt.nodes.new("ShaderNodeCombineColor")
    g = _grime(nt, L, coord, low=0.4)
    for i in range(3):
        L.new(g, grime.inputs[i])
    L.new(grime.outputs["Color"], dirty.inputs["B"])
    col = nt.nodes.new("ShaderNodeMix")
    col.data_type = "RGBA"
    L.new(mask.outputs["Result"], col.inputs["Factor"])
    L.new(dirty.outputs["Result"], col.inputs["A"])
    col.inputs["B"].default_value = (*metal, 1)
    L.new(col.outputs["Result"], b.inputs["Base Color"])
    L.new(mask.outputs["Result"], b.inputs["Metallic"])
    rough = nt.nodes.new("ShaderNodeMapRange")              # paint 0.5, bare metal 0.3
    rough.inputs["To Min"].default_value = 0.5
    rough.inputs["To Max"].default_value = 0.3
    L.new(mask.outputs["Result"], rough.inputs["Value"])
    L.new(rough.outputs["Result"], b.inputs["Roughness"])
    _bump(nt, L, coord, b, scale=60.0, strength=0.04)
    return m


def wood(name="Wood", light=(0.45, 0.28, 0.14), dark=(0.2, 0.11, 0.05), grain_scale=4.0):
    """Wood grain along object X: stretched wave bands + noise, varnish-ish."""
    m, nt, L, b = _principled(name)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    stretch = nt.nodes.new("ShaderNodeMapping")
    stretch.inputs["Scale"].default_value = (0.15, 1.0, 1.0)
    wave = nt.nodes.new("ShaderNodeTexWave")
    wave.wave_type = "BANDS"
    wave.bands_direction = "Y"
    wave.inputs["Scale"].default_value = grain_scale
    wave.inputs["Distortion"].default_value = 6.0
    wave.inputs["Detail"].default_value = 4.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*dark, 1)
    ramp.color_ramp.elements[1].color = (*light, 1)
    L.new(coord.outputs["Object"], stretch.inputs["Vector"])
    L.new(stretch.outputs["Vector"], wave.inputs["Vector"])
    L.new(wave.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.45
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.08
    L.new(wave.outputs["Fac"], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def rock_material(name="RockMat", light=(0.32, 0.3, 0.27), dark=(0.08, 0.075, 0.07)):
    """Stone: layered noise + voronoi cracks, strong bump, rough."""
    m, nt, L, b = _principled(name)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 3.0
    noise.inputs["Detail"].default_value = 10.0
    vor = nt.nodes.new("ShaderNodeTexVoronoi")
    vor.feature = "DISTANCE_TO_EDGE"
    vor.inputs["Scale"].default_value = 6.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*dark, 1)
    ramp.color_ramp.elements[1].color = (*light, 1)
    L.new(coord.outputs["Object"], noise.inputs["Vector"])
    L.new(coord.outputs["Object"], vor.inputs["Vector"])
    L.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.85
    crack = nt.nodes.new("ShaderNodeMath")
    crack.operation = "MULTIPLY"
    L.new(noise.outputs["Fac"], crack.inputs[0])
    L.new(vor.outputs["Distance"], crack.inputs[1])
    bump = nt.nodes.new("ShaderNodeBump")
    bump.inputs["Strength"].default_value = 0.35
    L.new(crack.outputs[0], bump.inputs["Height"])
    L.new(bump.outputs["Normal"], b.inputs["Normal"])
    return m


def glass(name="Glass"):
    m, _, _, b = _principled(name)
    b.inputs["Base Color"].default_value = (0.6, 0.7, 0.75, 1)
    b.inputs["Roughness"].default_value = 0.03
    b.inputs["Transmission Weight"].default_value = 0.9
    if "Thin Wall" in b.inputs:  # flat pane: no refraction offset
        b.inputs["Thin Wall"].default_value = True
    return m


def interior(name, color, strength):
    """What you see through a window: noisy warm/cool glow, darker in patches."""
    m, nt, L, b = _principled(name)
    b.inputs["Base Color"].default_value = (0.05, 0.05, 0.05, 1)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.7
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].color = (*[c * 0.25 for c in color], 1)
    ramp.color_ramp.elements[1].color = (*color, 1)
    L.new(coord.outputs["Object"], noise.inputs["Vector"])
    L.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], b.inputs["Emission Color"])
    b.inputs["Emission Strength"].default_value = strength
    return m


def city_windows(name="CityWindows", bay=1.2, floor=2.6, lit=0.9, strength=1.2):
    """Cheap material for BACKGROUND buildings: dark mass + scattered lit windows.
    Never use it on the hero object; that one gets real window geometry."""
    m, nt, L, b = _principled(name)
    b.inputs["Base Color"].default_value = (0.01, 0.012, 0.016, 1)
    b.inputs["Roughness"].default_value = 0.5
    coord = nt.nodes.new("ShaderNodeTexCoord")
    mp = nt.nodes.new("ShaderNodeMapping")
    mp.inputs["Scale"].default_value = (1 / bay, 1 / bay, 1 / floor)
    fl = nt.nodes.new("ShaderNodeVectorMath")
    fl.operation = "FLOOR"
    fr = nt.nodes.new("ShaderNodeVectorMath")
    fr.operation = "FRACTION"
    sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    wn = nt.nodes.new("ShaderNodeTexWhiteNoise")
    wn.noise_dimensions = "3D"
    L.new(coord.outputs["Object"], mp.inputs["Vector"])
    L.new(mp.outputs["Vector"], fl.inputs[0])
    L.new(mp.outputs["Vector"], fr.inputs[0])
    L.new(fr.outputs["Vector"], sep.inputs["Vector"])
    L.new(fl.outputs["Vector"], wn.inputs["Vector"])
    on = nt.nodes.new("ShaderNodeMath")
    on.operation = "GREATER_THAN"
    on.inputs[1].default_value = lit
    L.new(wn.outputs["Value"], on.inputs[0])
    sill = nt.nodes.new("ShaderNodeMath")
    sill.operation = "LESS_THAN"
    sill.inputs[1].default_value = 0.45
    L.new(sep.outputs["Z"], sill.inputs[0])
    mask = nt.nodes.new("ShaderNodeMath")
    mask.operation = "MULTIPLY"
    L.new(on.outputs[0], mask.inputs[0])
    L.new(sill.outputs[0], mask.inputs[1])
    st = nt.nodes.new("ShaderNodeMath")
    st.operation = "MULTIPLY"
    st.inputs[1].default_value = strength
    L.new(mask.outputs[0], st.inputs[0])
    tint = nt.nodes.new("ShaderNodeValToRGB")
    tint.color_ramp.elements[0].color = (1.0, 0.7, 0.4, 1)
    tint.color_ramp.elements[1].color = (0.7, 0.85, 1.0, 1)
    L.new(wn.outputs["Color"], tint.inputs["Fac"])
    L.new(tint.outputs["Color"], b.inputs["Emission Color"])
    L.new(st.outputs[0], b.inputs["Emission Strength"])
    return m


def wet_asphalt(name="WetAsphalt"):
    """Dark road with puddles: roughness from noise so lights reflect in patches."""
    m, nt, L, b = _principled(name)
    b.inputs["Base Color"].default_value = (0.012, 0.012, 0.014, 1)
    coord = nt.nodes.new("ShaderNodeTexCoord")
    noise = nt.nodes.new("ShaderNodeTexNoise")
    noise.inputs["Scale"].default_value = 0.35
    noise.inputs["Detail"].default_value = 6.0
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.45
    ramp.color_ramp.elements[0].color = (0.02, 0.02, 0.02, 1)
    ramp.color_ramp.elements[1].position = 0.6
    ramp.color_ramp.elements[1].color = (0.35, 0.35, 0.35, 1)
    L.new(coord.outputs["Object"], noise.inputs["Vector"])
    L.new(noise.outputs["Fac"], ramp.inputs["Fac"])
    L.new(ramp.outputs["Color"], b.inputs["Roughness"])
    _bump(nt, L, coord, b, scale=60.0, strength=0.08)
    return m


# ============================================================ surface tools
def _poly_frame(obj, poly):
    """World-space centre, normal and tangent basis of a polygon."""
    mw = obj.matrix_world
    nm = mw.to_3x3().inverted().transposed()
    c = mw @ poly.center
    n = (nm @ poly.normal).normalized()
    v0 = mw @ obj.data.vertices[poly.vertices[0]].co
    v1 = mw @ obj.data.vertices[poly.vertices[1]].co
    t = (v1 - v0)
    t = (t - n * t.dot(n)).normalized() if t.length > 1e-9 else n.orthogonal().normalized()
    b = n.cross(t)
    return c, n, Matrix((t, b, n)).transposed()


def _random_point(obj, poly, rng, spread=0.7):
    """Random point inside a (convex) polygon, in world space."""
    mw = obj.matrix_world
    corner = obj.data.vertices[rng.choice(list(poly.vertices))].co
    return mw @ poly.center.lerp(corner, rng.uniform(0, spread))


def subdivide(obj, cuts=4):
    """Grid-subdivide every edge of a mesh (panel_lines needs faces smaller than
    the panel size)."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bmesh.ops.subdivide_edges(bm, edges=bm.edges[:], cuts=cuts, use_grid_fill=True)
    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()


def panel_lines(obj, panel_size=0.4, seam=0.01, depth=0.006, raised=0.15, seed=0, flat=True):
    """Cut V-groove panel seams into ANY mesh and raise some panels.

    Faces are grouped into panels by a jittered 3D grid: cells of
    2 x `panel_size` that are randomly kept whole or split along some axes, so
    panel sizes vary. Each panel gets an inset ring that dips by `depth` and comes
    back up, which renders as a real seam line. A fraction `raised` of panels
    end up standing proud by `depth`. The mesh must have faces smaller than
    `panel_size` (use subdivide() first). Works on hulls, vehicles, props,
    armour, walls: anything hard-surface.

    Shading is set flat (`flat=True`): smooth shading blurs the seam slopes into
    the panels and they look like pillows. If you bevel the object afterwards,
    keep the bevel narrower than `seam` and use limit_method "ANGLE" with
    angle_limit ~80 deg: seam walls are 50-70 deg steep and a wide bevel turns
    every panel into a rounded block."""
    rng = random.Random(seed)
    off = Vector((rng.random(), rng.random(), rng.random())) * panel_size
    big = panel_size * 2
    # each coarse cell picks its own split, so panel sizes vary (identical
    # panels on a regular grid read as tiles or bricks)
    splits = [(1, 1, 1), (2, 1, 1), (1, 2, 1), (1, 1, 2), (2, 2, 1), (1, 2, 2), (2, 1, 2), (2, 2, 2)]
    choice = {}
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    bm.faces.ensure_lookup_table()
    groups = {}
    for f in bm.faces:
        c = f.calc_center_median() + off
        coarse = (math.floor(c.x / big), math.floor(c.y / big), math.floor(c.z / big))
        if coarse not in choice:
            choice[coarse] = rng.choice(splits)
        sx, sy, sz = choice[coarse]
        fine = tuple(math.floor((c[i] - coarse[i] * big) / (big / (sx, sy, sz)[i])) for i in range(3))
        groups.setdefault(coarse + fine, []).append(f)
    regions = list(groups.values())
    for faces in regions:
        faces = [f for f in faces if f.is_valid]
        if not faces:
            continue
        r1 = bmesh.ops.inset_region(bm, faces=faces, thickness=seam * 0.5, depth=-depth,
                                    use_even_offset=True)
        inner = [f for f in faces if f.is_valid]
        lift = depth * (1.6 if rng.random() < raised else 1.0)
        bmesh.ops.inset_region(bm, faces=inner, thickness=seam * 0.5, depth=lift,
                               use_even_offset=True)
    bm.normal_update()
    bm.to_mesh(obj.data)
    bm.free()
    for p in obj.data.polygons:
        p.use_smooth = not flat
    obj.data.update()
    return len(regions)


def greeble(target, builder, count=120, size=(0.04, 0.25), height=(0.01, 0.12),
            seed=0, facing=None, kinds=("box", "box", "cyl", "stack"), embed=0.012):
    """Place `count` small parts on the surface of `target` into `builder`.

    Parts are boxes, cylinders (along the normal) or stepped stacks, aligned to
    the face normal, sized in metres. `facing=(0, 0, 1), 0.7` restricts them to
    faces whose normal points within ~45 deg of that direction (e.g. only the
    top). Area-weighted, so big faces get more parts. Every part sinks
    `embed` metres into the surface, so it is attached, not resting on it."""
    bpy.context.view_layer.update()      # matrix_world is stale right after moving the target
    rng = random.Random(seed)
    polys = list(target.data.polygons)
    if facing is not None:
        direction, min_dot = Vector(facing[0]).normalized(), facing[1]
        nm = target.matrix_world.to_3x3().inverted().transposed()
        polys = [p for p in polys if (nm @ p.normal).normalized().dot(direction) >= min_dot]
    if not polys:
        return 0
    for poly in rng.choices(polys, weights=[p.area for p in polys], k=count):
        _, n, basis = _poly_frame(target, poly)
        p = _random_point(target, poly, rng)
        w = rng.uniform(*size)
        h = rng.uniform(*height)
        kind = rng.choice(kinds)
        builder.tag = f"greeble on {target.name}"
        if kind == "cyl":
            builder.cyl(p - n * embed, p + n * h, w / 2, segments=12)
        elif kind == "stack":
            builder.box(p + n * ((h - embed) / 2), (w, w * rng.uniform(0.5, 1.2), h + embed), basis)
            builder.box(p + n * (h + h / 4 - embed / 2), (w * 0.6, w * 0.4, h / 2 + embed), basis)
        else:
            builder.box(p + n * ((h - embed) / 2), (w, w * rng.uniform(0.3, 1.5), h + embed), basis)
    return count


def scatter_instances(target, source, count=50, scale=(0.7, 1.3), align=True,
                      seed=0, facing=None, offset=0.0, collection=None):
    """Spread linked duplicates of `source` over `target`'s surface (cheap:
    they share one mesh). Random spin around the normal and random scale.
    Use a small negative `offset` to sink them into the surface.
    Returns the new objects."""
    bpy.context.view_layer.update()      # matrix_world is stale right after moving the target
    rng = random.Random(seed)
    polys = list(target.data.polygons)
    if facing is not None:
        direction, min_dot = Vector(facing[0]).normalized(), facing[1]
        nm = target.matrix_world.to_3x3().inverted().transposed()
        polys = [p for p in polys if (nm @ p.normal).normalized().dot(direction) >= min_dot]
    out = []
    coll = collection or bpy.context.scene.collection
    for i, poly in enumerate(rng.choices(polys, weights=[p.area for p in polys], k=count)):
        _, n, _ = _poly_frame(target, poly)
        o = bpy.data.objects.new(f"{source.name}.{i:03d}", source.data)
        coll.objects.link(o)
        o.location = _random_point(target, poly, rng, spread=0.95) + n * offset
        spin = Matrix.Rotation(rng.uniform(0, 2 * math.pi), 4, "Z")
        rot = (Z.rotation_difference(n).to_matrix().to_4x4() if align else Matrix.Identity(4)) @ spin
        o.rotation_euler = rot.to_euler()
        o.scale = (rng.uniform(*scale),) * 3
        out.append(o)
    return out


def apply_modifiers(obj):
    """Bake modifiers into the mesh (needed before sharing it via instances)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(obj.evaluated_get(dg))
    old = obj.data
    obj.modifiers.clear()
    obj.data = me
    if old.users == 0:
        bpy.data.meshes.remove(old)
    return obj


def rock(name="Rock", radius=0.5, seed=0, material=None, flatten=(0.55, 0.9)):
    """Irregular rock: icosphere + two displacement layers (big lumps, small
    chips), squashed, modifiers applied. Use with scatter_instances()."""
    rng = random.Random(seed)
    me = bpy.data.meshes.new(name)
    bm = bmesh.new()
    bmesh.ops.create_icosphere(bm, subdivisions=4, radius=radius)
    bm.to_mesh(me)
    bm.free()
    for p in me.polygons:
        p.use_smooth = True
    obj = bpy.data.objects.new(name, me)
    bpy.context.scene.collection.objects.link(obj)
    for i, (kind, scale, strength) in enumerate((("CLOUDS", 0.9, 0.45), ("VORONOI", 0.25, 0.08))):
        tex = bpy.data.textures.new(f"{name}Tex{i}", type=kind)
        tex.noise_scale = radius * scale
        mod = obj.modifiers.new(f"Displace{i}", "DISPLACE")
        mod.texture = tex
        mod.texture_coords = "LOCAL"
        mod.strength = radius * strength
        mod.mid_level = 0.5 + rng.uniform(-0.05, 0.05)
    apply_modifiers(obj)
    obj.scale = (1.0, rng.uniform(0.75, 1.0), rng.uniform(*flatten))
    obj.data.materials.append(material or rock_material())
    return obj


def cable(p0, p1, sag=0.15, radius=0.01, material=None, name="Cable"):
    """Hanging cable / hose / wire between two points; `sag` is the dip as a
    fraction of the span."""
    p0, p1 = Vector(p0), Vector(p1)
    curve = bpy.data.curves.new(name, "CURVE")
    curve.dimensions = "3D"
    curve.bevel_depth = radius
    curve.bevel_resolution = 3
    curve.resolution_u = 16
    sp = curve.splines.new("BEZIER")
    sp.bezier_points.add(2)
    mid = (p0 + p1) / 2 - Z * sag * (p1 - p0).length
    for bp, co in zip(sp.bezier_points, (p0, mid, p1)):
        bp.co = co
        bp.handle_left_type = bp.handle_right_type = "AUTO"
    obj = bpy.data.objects.new(name, curve)
    bpy.context.scene.collection.objects.link(obj)
    curve.materials.append(material or simple("CableRubber", (0.01, 0.01, 0.01), rough=0.45))
    return obj


