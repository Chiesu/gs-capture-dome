# SPDX-License-Identifier: GPL-3.0-or-later
"""GS Capture Dome, Blender Extension entry point.

The implementation is kept in gs_capture_dome.py so it can also be run as
an independent Blender Python script. Do not rename its operators or scene
properties when changing the public release packaging.
"""

from . import gs_capture_dome


def register():
    gs_capture_dome.register()


def unregister():
    gs_capture_dome.unregister()
