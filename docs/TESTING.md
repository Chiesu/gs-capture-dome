# Blender Release Validation Checklist

Run these checks in Blender 4.2+ on a disposable `.blend` file before calling this release Blender-tested.

- [ ] Install `gs_capture_dome-0.5.0.zip` using Preferences > Get Extensions > Install from Disk.
- [ ] Enable GS Capture Dome; verify Viewport N sidebar > GS Capture appears with no errors.
- [ ] Test Orbit mode around a simple mesh with the 3D Cursor target.
- [ ] Generate Legacy 26-camera dome; check the exact count (26).
- [ ] Generate Exact Camera Count with 10 cameras; check the exact count (10).
- [ ] Generate Adaptive Rings; change count/density and regenerate.
- [ ] Choose a Reference Camera, build from it, then test framing scale/lens matching.
- [ ] Enable collision against a plane or evaluated terrain mesh; verify skipped candidates.
- [ ] Test Fixed Position / Look Around with 8 views, move the reference camera, and verify the generated rig follows.
- [ ] Render 3 selected cameras to a temporary folder at low resolution (PNG).
- [ ] Check that the expected `view_###.png` files exist and parse `transforms.json`.
- [ ] Stop an active queue; verify no stuck progress state, existing images remain, and scene camera/resolution/path return to their originals.
- [ ] Regenerate after Stop, then test Reset Render State if necessary.
- [ ] Delete the generated rig; verify only generated objects were deleted.
- [ ] Disable and re-enable the extension, close and reopen Blender, then check the panel loads normally.

## Static checks already available

```bash
python3 scripts/build_release.py
python3 -m unittest discover -s tests -v
```

These are packaging/source checks, **not a substitute** for a Blender session. For a bug report attach Blender version, OS, minimal reproducible `.blend` file (if shareable), console traceback, mode and steps to reproduce.
