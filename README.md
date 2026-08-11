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
  context-dependent `bpy.ops`, with proper context overrides when operators are
  unavoidable. Runs headless (`blender --background --python`).
- **Geometry & correct joining** — deliberate choice between *join* (one object),
  *boolean* (watertight solid), and *parent* (grouped but separate); merging
  coincident vertices, recalculating normals, manifold checks.
- **Materials & textures** — node-based Principled BSDF, PBR maps, UV unwrapping,
  and the correct color spaces (sRGB vs Non-Color).
- **Lighting** — three-point rigs, HDRI world lighting, physically-scaled light
  power, and view transform / color management.
- **Animation** — keyframes, F-curve easing, drivers, constraints, armatures,
  shape keys, path following.
- **Rendering & export** — Cycles vs Eevee, samples/denoise, cameras, and export
  to glTF / FBX / OBJ / STL / USD.
- **Version awareness** — detects `bpy.app.version` and discovers socket/attribute
  names at runtime instead of hardcoding, so it survives Blender updates.

Tested against **Blender 4.5 LTS** and **5.x** (incl. 5.2 LTS). Version-sensitive
APIs are checked at runtime rather than assumed.

## Repository layout

```
blender/
├── SKILL.md                 # entry point: workflow + core rules
├── references/
│   ├── geometry.md          # creating meshes; join vs boolean vs parent; normals
│   ├── materials.md         # nodes, PBR, UVs, color spaces
│   ├── lighting.md          # light types, three-point, HDRI, color management
│   ├── animation.md         # keyframes, drivers, constraints, rigging
│   ├── rendering.md         # engines, output, cameras, export
│   ├── objects_2d.md        # curves, text, grease pencil, sprites, SVG
│   └── gotchas.md           # version differences & footguns
└── scripts/
    └── boilerplate.py       # clean scene + auto-camera + render helper
```

## Install

### Claude Code

Global (all projects):

```bash
git clone https://github.com/<your-user>/claude-blender-skill.git
cp -r claude-blender-skill/blender ~/.claude/skills/
```

Or per-project — copy the `blender/` folder into `.claude/skills/` inside your
repo. The skill loads automatically when a task involves Blender.

### Claude apps / claude.ai

Zip the `blender/` folder and upload it via **Settings → Capabilities → Skills**
(where available on your plan).

## Requirements

- **Blender 4.5 LTS or 5.x** on the machine that runs the generated scripts.
  The skill writes Python that you (or Claude Code) run via
  `blender --background --factory-startup --python scene.py`.
- The skill itself has no dependencies; it's Markdown plus one boilerplate script.

## Usage

Once installed, just ask in natural language, e.g.:

- "Model a low-poly tree and render it on a transparent background."
- "Take these two meshes and join them into one watertight solid."
- "Set up three-point lighting and a 50 mm camera framing the object."
- "Animate the cube bouncing for 3 seconds and export a PNG sequence."

Claude will consult the skill, write a `bpy` script, run it headless if Blender is
available, and inspect the render before reporting back.

## License

MIT — see [LICENSE](LICENSE).

## Notes

This is a community skill and is not affiliated with the Blender Foundation.
Blender is licensed under the GNU GPL; this repository only contains Python scripts
and documentation that use Blender's public Python API.