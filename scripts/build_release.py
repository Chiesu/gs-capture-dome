#!/usr/bin/env python3
"""Build the installable GS Capture Dome extension ZIP using stdlib only."""

from __future__ import annotations

import ast
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIP_FILES = (
    "blender_manifest.toml",
    "__init__.py",
    "gs_capture_dome.py",
    "LICENSE",
)


def get_release_version() -> str:
    with (ROOT / "blender_manifest.toml").open("rb") as f:
        data = tomllib.load(f)
    if data.get("id") != "gs_capture_dome":
        raise ValueError("Unexpected extension ID; do not change saved identity")
    if data.get("type") != "add-on":
        raise ValueError("Manifest type must be add-on")
    version = str(data["version"])
    if not version or "/" in version or ".." in version:
        raise ValueError("Invalid version")
    return version


def check_source_version(version: str) -> None:
    source = (ROOT / "gs_capture_dome.py").read_text(encoding="utf-8")
    module = ast.parse(source)
    assignment = next(
        (node for node in module.body
         if isinstance(node, ast.Assign)
         and any(isinstance(t, ast.Name) and t.id == "bl_info" for t in node.targets)),
        None,
    )
    if assignment is None:
        raise ValueError("Blender bl_info metadata not found")
    bl_info = ast.literal_eval(assignment.value)
    source_version = ".".join(str(x) for x in bl_info["version"])
    if source_version != version:
        raise ValueError(f"Version mismatch: manifest {version}, code {source_version}")
    compile(source, "gs_capture_dome.py", "exec")
    compile((ROOT / "__init__.py").read_text(encoding="utf-8"), "__init__.py", "exec")


def build() -> Path:
    version = get_release_version()
    check_source_version(version)
    out = ROOT / "dist" / f"gs_capture_dome-{version}.zip"
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        for name in SHIP_FILES:
            filepath = ROOT / name
            if not filepath.is_file():
                raise FileNotFoundError(filepath)
            zf.write(filepath, arcname=name)

    with zipfile.ZipFile(out) as zf:
        if zf.testzip() is not None:
            raise ValueError("Corrupt ZIP member")
        if set(zf.namelist()) != set(SHIP_FILES):
            raise ValueError("Release ZIP has unexpected entries")
    return out


if __name__ == "__main__":
    try:
        output_path = build()
    except (OSError, KeyError, ValueError, SyntaxError) as exc:
        print(f"BUILD FAILED: {exc}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Built: {output_path}")
