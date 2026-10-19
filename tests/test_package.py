"""Static public-release checks (no Blender runtime required)."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ReleaseTests(unittest.TestCase):
    def test_manifest(self):
        with (ROOT / "blender_manifest.toml").open("rb") as f:
            d = tomllib.load(f)
        self.assertEqual(d["schema_version"], "1.0.0")
        self.assertEqual(d["id"], "gs_capture_dome")
        self.assertEqual(d["type"], "add-on")
        self.assertEqual(d["version"], "0.5.0")
        self.assertGreaterEqual(tuple(map(int, d["blender_version_min"].split("."))), (4, 2, 0))
        self.assertEqual(d["license"], ["SPDX:GPL-3.0-or-later"])
        self.assertIn("files", d["permissions"])

    def test_bl_info_and_original_operators(self):
        source = (ROOT / "gs_capture_dome.py").read_text(encoding="utf-8")
        module = ast.parse(source)
        info = ast.literal_eval(next(
            n.value for n in module.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in n.targets)
        ))
        self.assertEqual(info["version"], (0, 5, 0))
        self.assertIn('bl_idname = "gs_capture.build_rig"', source)
        self.assertIn('bl_idname = "gs_capture.render_views"', source)
        self.assertIn('bpy.types.Scene.gs_capture =', source)
        self.assertIn('bpy.types.Object.gs_render_enabled =', source)
        self.assertIn('LOOK_AROUND', source)
        self.assertIn('transforms.json', source)
        self.assertNotIn('"author": "OpenAI"', source)

    def test_source_compiles(self):
        for filename in ("gs_capture_dome.py", "__init__.py", "scripts/build_release.py"):
            p = ROOT / filename
            compile(p.read_text(encoding="utf-8"), str(p), "exec")

    def test_release_zip(self):
        subprocess.run([sys.executable, str(ROOT / "scripts" / "build_release.py")], check=True, capture_output=True)
        p = ROOT / "dist" / "gs_capture_dome-0.5.0.zip"
        self.assertTrue(p.exists())
        with zipfile.ZipFile(p) as zf:
            self.assertIsNone(zf.testzip())
            self.assertEqual(set(zf.namelist()), {
                "__init__.py", "gs_capture_dome.py", "blender_manifest.toml", "LICENSE"
            })
            self.assertFalse(any("/" in path for path in zf.namelist()))
            self.assertIn("gs_capture_dome.register()", zf.read("__init__.py").decode())


if __name__ == "__main__":
    unittest.main()
