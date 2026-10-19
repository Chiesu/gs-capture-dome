# GS Capture Dome v0.5.0

Blender multi-camera capture add-on for Gaussian Splatting experiments and synthetic multi-view rendering.

## Features

- **Orbit capture:** Adaptive Rings, Exact Camera Count (Fibonacci), or Legacy 26-Camera Dome.
- **Fixed Position / Look Around:** multiple orientations from a single reference camera origin.
- **Rig control:** target object or 3D Cursor, reference camera, framing and radius/lens matching.
- **Ground clearance:** exclude candidate cameras that intersect terrain.
- **Batch rendering:** PNG/JPEG/EXR, resolution presets, camera selection, progress, retry and Stop.
- **Camera export:** optional `transforms.json` with per-image camera poses and intrinsics.

## Installation

1. Download the attached **`gs_capture_dome-0.5.0.zip`** file. Do not download the GitHub-generated source archive.
2. Blender 4.2+ > Edit > Preferences > Get Extensions > Install from Disk.
3. Select the downloaded ZIP without extracting it, then enable the extension.
4. Open the viewport **N** sidebar > **GS Capture**.

**Compatibility:** Blender 4.2+ is declared by the source. Python syntax, extension manifest and ZIP structure have been validated; a live Blender rendering test is still required.

**Notes:** The add-on creates rendered images and camera metadata; it does not train splats or generate COLMAP datasets. Single-origin Look Around views contain no translation/parallax. Files can be overwritten on repeat exports to the same folder. See the README for limitations.

**License:** GPL-3.0-or-later.
