# Quick Start

## Orbit Target / Sphere

1. Save the `.blend` file. Use a simple object and camera to test first.
2. Viewport > N sidebar > GS Capture. Leave Capture Approach on **Orbit Target / Sphere**.
3. Set the target with **Set Selected as Target**, or choose Target Mode > **3D Cursor**.
4. Choose **Adaptive Rings**, **Exact Camera Count** or **Legacy 26-Camera Dome**.
5. Set Radius, Dome Angle (up to 90 degrees from the dome pole), Lens and Sensor Width.
6. Select **Build Capture Dome**. A generated `GS_CAPTURE_RIG` collection and cameras appear in the Outliner.
7. To use an existing viewpoint as the pole/center, select a non-GS reference camera and click **Set Selected as Reference Camera** followed by **Build From Reference Camera**.
8. Optional: enable **Ground Collision** and select a mesh representing terrain. Cameras below the evaluated surface plus clearance are skipped.
9. Change rig rotation via **Orbit / Framing > Rig Rotation**, or select `GS_RIG_CONTROL` directly.

### Framing / Scale

If you have a reference camera, capture the initial framing baseline, adjust Rig Scale, then **Apply Scale + Regenerate** to move the dome closer/farther while matching the lens approximately for target-plane framing. This does not preserve identical parallax or field of view throughout the scene.

## Fixed Position / Look Around

1. Select Capture Approach > **Fixed Position / Look Around**.
2. Choose the **Reference Camera** and View Count.
3. Click **Build From Reference Camera**. Every new camera occupies that reference camera position but looks in a different direction.
4. Change the reference camera's transforms to move/rotate the full generated setup.

No subject translation/parallax is created in this mode. Choose Orbit for multi-baseline 3D reconstruction.

## Batch render

1. Set Output Folder. `//gs_capture/` is relative to the saved `.blend`.
2. Select PNG / JPEG / OpenEXR, output preset and whether to Apply Compositor / Post FX.
3. Enable Write `transforms.json` if camera metadata is required.
4. Choose All Cameras or Selected Cameras; for the latter, enable the desired entries in the list.
5. Click **Render All GS Views**, inspect progress and stop if necessary.
6. Check the output folder for `view_000.png`, etc., and `transforms.json` if enabled.

Changes to the rig while rendering can invalidate the result. Stop the queue and regenerate instead.
