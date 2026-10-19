# Known Limitations and Compatibility

1. **Single managed rig:** The implementation addresses a fixed `GS_CAPTURE_RIG` collection and named helper objects. Use one active rig per `.blend` file. Do not place unrelated scene objects inside it; deleting/rebuilding the rig can delete its contents.
2. **Overwrites:** Captures in the same output folder use `view_###` filenames. Repeated output can overwrite images and `transforms.json`. Create distinct output folders for separate capture sessions.
3. **No cross-session resume:** Retries operate within the active Blender render queue. Stop preserves completed images, but a later session needs explicit camera selection for unfinished views.
4. **Not a GS trainer:** No Gaussian Splatting fitting, point-cloud creation, COLMAP project, depth maps, segmentation masks or PLY exporter. Use a compatible downstream pipeline.
5. **Metadata conventions:** `transforms.json` contains Blender world-space camera-to-world poses, intrinsics, paths and a camera model label. Downstream reconstruction systems may require coordinate conversion and/or additional metadata.
6. **No parallax in Look Around:** Same camera origin from multiple orientations does not supply the translation baselines typical multi-view reconstruction methods need.
7. **Simple terrain avoidance:** World-Z raycasts against one evaluated mesh are not a general collision solver. Camera bodies and foreground occluders are not tested.
8. **Frame consistency:** Captures the current frame. Temporal simulations, changing material noise, moving foliage, reflections and inconsistent light can reduce matching quality.
9. **Output size:** Resolution presets are capped at 2x the Blender scene's base width and height.
10. **Compatibility:** Minimum Blender 4.2 is declared by the original source. Static checks passed in the packaging environment, but actual installation and modal render behavior still need manual Blender testing. Report the exact Blender version and traceback in a bug report.

For testing steps see [TESTING.md](TESTING.md).
