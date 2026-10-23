# GS Capture Dome

A Blender extension for generating multi-camera rigs and rendering organized image sets for Gaussian Splatting experiments, synthetic data capture and photogrammetry-style workflows.

**Version:** 0.5.0 | **Blender:** 4.2+ (declared; runtime validation pending) | **License:** GPL-3.0-or-later | **Dependencies:** Blender only

GS Capture Dome is a camera capture and export utility. It does **not** reconstruct or train Gaussian splats, generate `.ply` files, or create COLMAP projects.

<img width="1920" height="1080" alt="hero" src="https://github.com/user-attachments/assets/9be52621-d4b2-4f37-a2fe-648aa50131eb" />

## Download / install

1. Open [Releases](https://github.com/Chiesu/gs-capture-dome/releases/latest) and download **`gs_capture_dome-0.5.0.zip`** from the release assets. Do not use GitHub's automatically generated **Source code (zip)**.
2. In Blender, choose **Edit > Preferences > Get Extensions > Install from Disk** (the menu location can vary between Blender versions).
3. Select the ZIP **without extracting it** and enable **GS Capture Dome** if prompted.
4. In the 3D Viewport, open the **N** sidebar > **GS Capture**.

For a legacy single-file script, use [`gs_capture_dome.py`](gs_capture_dome.py) inside Blender's Scripting workspace. The extension ZIP is preferred for normal installation.

## Capture approaches

<img width="1920" height="1080" alt="dome-setup" src="https://github.com/user-attachments/assets/7f163177-9cdf-4733-9098-1268a1ad3138" />

### 1. Orbit Target / Sphere (default)

Place multiple cameras on a spherical cap aimed inward at a target. Useful when you need multiple viewpoints and camera-baseline parallax around a subject.

- **Adaptive Rings:** adjustable latitude ring count and camera density.
- **Exact Camera Count:** Fibonacci distribution with an explicit number of viewpoints.
- **Legacy 26-Camera Dome:** fixed `1 + 5 + 8 + 12` hemisphere configuration.
- Use a mesh object's origin, bounding-box center, or the 3D Cursor as the target.
- Optionally build the dome from an existing Reference Camera to preserve its general location, distance, optics and horizon.
- Optional terrain collision filters out candidate viewpoints below a selected mesh surface plus clearance; supports evaluated Geometry Nodes terrain.
- An orbit control and non-rendering wireframe guide help position the rig.

### 2. Fixed Position / Look Around

Generate different camera orientations from the **same** Reference Camera position, with a selectable number of views. The generated rig follows the reference camera's transforms.

**Important:** views from a single fixed origin do not provide multi-view translation/parallax. This mode alone is generally **not sufficient for 3D Gaussian Splatting reconstruction**. Use the orbit approach when you need reconstruction baselines.

## Batch render and export

<img width="1920" height="1080" alt="batch-export" src="https://github.com/user-attachments/assets/6b4edf03-b98b-499d-8fe9-b7f22e8100b3" />

- Renders views sequentially using the current Blender render engine.
- Outputs PNG, JPEG or OpenEXR using a custom percentage or height preset, up to 2x the scene's base dimensions.
- Choose all generated cameras or enable individual cameras for a selective batch.
- Progress, Stop and interruption retries are built into the render queue. Existing completed images remain on disk after Stop.
- Optional **`transforms.json`** with per-view camera-to-world transforms, intrinsics and image paths.
- Optional use of the scene compositor. The add-on restores the scene's temporary render/camera overrides after the queue finishes or stops.

Output defaults to `//gs_capture/`, relative to the saved `.blend` file. Save the project first and select an output folder with sufficient disk space.

## Quick start

1. Open a saved Blender scene. Select a subject mesh, or set the target to the 3D Cursor.
2. Open **N > GS Capture**, use **Orbit Target / Sphere**, and select the **Target Object** or 3D Cursor mode.
3. Choose **Adaptive Rings** (or **Exact Camera Count**) and adjust Radius, Dome Angle and optics.
4. Click **Build Capture Dome**. Inspect the viewport and generated `GS_CAPTURE_RIG` collection.
5. In **Render / Export**, select the output directory, image format, resolution and camera selection.
6. Click **Render All GS Views**. After completion, inspect the image files and optional `transforms.json`.

For a source/reference-camera workflow and field explanations, see [Quick Start](docs/QUICK_START.md).

## Files exported

```text
gs_capture/
  view_000.png
  view_001.png
  view_002.png
  ...
  transforms.json    # optional
```

The output image extension changes with the selected format. `transforms.json` uses an OpenCV camera-model label and Blender camera-to-world matrices (Blender world up: Z). **Consumer pipelines differ in their coordinate conventions and metadata requirements; conversion may be required.** This is not a native COLMAP export.

## Limitations and safe use

- Only one generated `GS_CAPTURE_RIG` collection is managed at a time. Regeneration/deletion can remove objects placed inside that generated collection; **do not put unrelated objects there**.
- Re-rendering to the same output folder can overwrite files with matching `view_###` names and `transforms.json`.
- Interrupted views can retry within one batch, but the extension does not automatically resume batches across Blender sessions. Select the remaining cameras manually if needed.
- Ground collision tests vertical clearance against one selected mesh, not full geometry-to-geometry collision.
- Only the active timeline frame is rendered. Moving objects, procedural noise, and changing lighting between cameras can reduce reconstruction consistency.
- Source syntax, manifest structure, ZIP contents and build scripts are checked. **A live Blender UI/render session has not been run in the packaging environment.** Test on a duplicate `.blend` first.

See [Known Limitations](docs/KNOWN_LIMITATIONS.md) and [Testing Checklist](docs/TESTING.md).

## Development and support

Source: [Chiesu/gs-capture-dome](https://github.com/Chiesu/gs-capture-dome) | [Issues](https://github.com/Chiesu/gs-capture-dome/issues)

To build the installable ZIP locally (Python 3.11+):

```bash
python3 scripts/build_release.py
python3 -m unittest discover -s tests -v
```

The Blender add-on itself has no third-party Python dependencies. The source is licensed GPL-3.0-or-later; see [LICENSE](LICENSE). Publication and release instructions are in [PUBLISHING.md](PUBLISHING.md).
