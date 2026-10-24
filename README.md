# Blender Agent Skill for Claude

An [Agent Skill](https://docs.claude.com/en/docs/agents-and-tools/agent-skills/overview)
that teaches Claude to drive **Blender** through its Python API (`bpy`) and produce
**clean, correct 3D/2D assets** instead of fragile scripts that silently break.

It focuses on the things that usually go wrong when an LLM writes Blender code:
context-dependent operators failing headless, duplicate vertices and flipped
normals, wrong texture color spaces, black or blown-out renders, and version drift
between Blender releases.

## What it covers

- **Robust scripting model** — prefer the `bpy.data` / `bmesh` data API over
  context-dependent `bpy.ops`, with context overrides when operators are
  unavoidable and a correct pattern for edit-mode operators. Runs headless and
  fails loudly (`--python-exit-code 1`).
- **Geometry & correct joining** — deliberate choice between *join* (one object),
  *boolean* (watertight solid), and *parent* (grouped but separate); merging
  coincident vertices, recalculating normals, manifold checks, bevels for
  believable hard-surface edges.
- **Materials & textures** — node-based Principled BSDF, PBR maps, UV unwrapping,
  and the correct color spaces (sRGB vs Non-Color).
- **Lighting** — seven size-aware presets (studio, high/low key, outdoor, golden
  hour, overcast, night neon) that scale light placement and power to the
  subject, physical sky / HDRI helpers, AgX looks, and automatic exposure check.
- **Animation** — keyframes, F-curve easing via 5.x slotted actions, drivers,
  constraints, armatures with automatic weights, shape keys, path following.
- **Rendering & export** — Cycles vs EEVEE (with GPU detection), the 5.x
  node-group compositor, cameras that frame the subject without cropping, and
  export to glTF / FBX / OBJ / STL / USD / PLY.
- **Self-checking renders** — `verify_render()` flags black, blown-out, flat,
  empty or cropped images; `render_views()` makes a 4-angle contact sheet so
  holes and flipped normals can't hide.
- **Blender 5.x API** — a table of 4.x code that breaks on 5.x and its
  replacement (`BLENDER_EEVEE_NEXT`, `action.fcurves`, `scene.node_tree`,
  `use_nodes`, Grease Pencil v3, removed exporters, …).

**Targets Blender 5.x.** Every code block in `references/` is executed by
`tests/run_snippets.py` and passes on **Blender 5.0.1 and 5.2.3 LTS**, with
DeprecationWarnings from the snippets treated as failures. Blender 4.x is not
supported: much of its API differs.

## Repository layout

```
blender/                       # the skill itself (this folder gets installed)
├── SKILL.md                   # entry point: workflow + core rules
├── references/
│   ├── geometry.md            # meshes; join vs boolean vs parent; normals; bevels
│   ├── materials.md           # nodes, PBR, UVs, color spaces
│   ├── lighting.md            # presets, units, ratios, sky/HDRI, exposure
│   ├── animation.md           # keyframes, slotted actions, drivers, rigging
│   ├── rendering.md           # engines, compositor, cameras, export
│   ├── objects_2d.md          # curves, text, Grease Pencil v3, sprites, SVG
│   └── gotchas.md             # 4.x -> 5.x breakages, footguns, checklist
└── scripts/
    ├── boilerplate.py         # clean scene, framing camera, render + QA, contact sheet
    └── lighting_presets.py    # size-aware lighting presets + auto-exposure
tests/                         # not part of the skill
├── run_snippets.py            # executes every python block in references/
└── test_lighting_presets.py   # checks preset exposure + scale independence
.github/workflows/test.yml     # runs both tests on Blender 5.0 and 5.2
```

## Install

### Claude Code

Global (all projects):

```bash
git clone https://github.com/MartinRapcan/blender-claude-skill.git
cp -r blender-claude-skill/blender ~/.claude/skills/
```

Or per-project — copy the `blender/` folder into `.claude/skills/` inside your
repo. The skill loads automatically when a task involves Blender.

### Claude apps / claude.ai

Zip the `blender/` folder and upload it via **Settings → Capabilities → Skills**
(where available on your plan).

## Requirements

- **Blender 5.0 or newer** (tested on 5.0.1 and 5.2.3 LTS) on the machine that
  runs the generated scripts:
  `blender -b --factory-startup --python-exit-code 1 -P scene.py`.
- No Blender install? `pip install bpy==5.2.3` (Python 3.13) gives a headless
  Blender as a Python module; run scripts with `python scene.py`.
- The skill itself has no dependencies: Markdown plus two Python helper scripts.

## Testing

```bash
python3.13 -m venv .venv && .venv/bin/pip install bpy==5.2.3
.venv/bin/python tests/run_snippets.py          # every code block in references/
.venv/bin/python tests/test_lighting_presets.py # preset calibration
```
For Blender 5.0 use Python 3.11 and `bpy==5.0.1`. CI runs both on every push
and weekly.

## Usage

Once installed, just ask in natural language, e.g.:

- "Model a low-poly tree and render it on a transparent background."
- "Take these two meshes and join them into one watertight solid."
- "Set up three-point lighting and a 50 mm camera framing the object."
- "Render this product shot with moody, dramatic lighting."
- "Animate the cube bouncing for 3 seconds and export a PNG sequence."

Claude will consult the skill, write a `bpy` script, run it headless if Blender is
available, check the render automatically and by looking at it, and iterate
before reporting back.

## License

MIT — see [LICENSE](LICENSE).

## Notes

This is a community skill and is not affiliated with the Blender Foundation.
Blender is licensed under the GNU GPL; this repository only contains Python scripts
and documentation that use Blender's public Python API.