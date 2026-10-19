# SPDX-License-Identifier: GPL-3.0-or-later
bl_info = {
    "name": "GS Capture Dome",
    "author": "Chiesu",
    "version": (0, 5, 0),
    "blender": (4, 2, 0),
    "location": "3D View > Sidebar > GS Capture",
    "description": "Build and render a hemispherical multi-camera capture rig for Gaussian Splatting / photogrammetry-style capture",
    "category": "Camera",
}

import bpy
import json
import math
import os
from mathutils import Matrix, Vector
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from bpy.types import Operator, Panel, PropertyGroup


RIG_COLLECTION = "GS_CAPTURE_RIG"
TARGET_NAME = "GS_TARGET"
ANCHOR_NAME = "GS_RIG_ANCHOR"
CONTROL_NAME = "GS_RIG_CONTROL"
GUIDE_NAME = "GS_DOME_GUIDE"
CAM_DATA_NAME = "GS_CameraData"
CAM_PREFIX = "GS_CAM_"


# Standard vertical-resolution presets. Presets preserve the scene's current
# aspect ratio. Rendering is capped at 2x the scene's base resolution.
RESOLUTION_PRESET_ITEMS = (
    ('CUSTOM', "Custom %", "Scale the scene's base resolution by a custom percentage"),
    ('ORIGINAL', "Original", "Render at the scene's base resolution"),
    ('P240', "240p", "Render at 240 pixels high while preserving the scene aspect ratio"),
    ('P360', "360p", "Render at 360 pixels high while preserving the scene aspect ratio"),
    ('P480', "480p", "Render at 480 pixels high while preserving the scene aspect ratio"),
    ('P720', "720p", "Render at 720 pixels high while preserving the scene aspect ratio"),
    ('P1080', "1080p", "Render at 1080 pixels high while preserving the scene aspect ratio"),
    ('P1440', "1440p", "Render at 1440 pixels high while preserving the scene aspect ratio"),
    ('P2160', "2160p / 4K", "Render at 2160 pixels high while preserving the scene aspect ratio"),
    ('DOUBLE', "2x Original", "Render at exactly twice the scene's base width and height"),
)

RESOLUTION_PRESET_HEIGHTS = {
    'P240': 240,
    'P360': 360,
    'P480': 480,
    'P720': 720,
    'P1080': 1080,
    'P1440': 1440,
    'P2160': 2160,
}


def _resolution_preset_items(self, context):
    """Only expose standard presets that fit inside the 2x-resolution cap."""
    scene = context.scene if context and context.scene else bpy.context.scene
    if scene is None:
        return RESOLUTION_PRESET_ITEMS

    base_h = max(1, int(scene.render.resolution_y))
    max_h = base_h * 2
    allowed = []
    for item in RESOLUTION_PRESET_ITEMS:
        identifier = item[0]
        preset_h = RESOLUTION_PRESET_HEIGHTS.get(identifier)
        if preset_h is None or preset_h <= max_h:
            allowed.append(item)
    return allowed


def _capture_output_dimensions(scene, settings):
    """Return (width, height, equivalent_percent) for the capture render.

    The base resolution is always the scene's resolution_x / resolution_y at
    100%. Standard presets target an exact output height and preserve that base
    aspect ratio. No mode can exceed 2x the base width or height.
    """
    base_w = max(1, int(scene.render.resolution_x))
    base_h = max(1, int(scene.render.resolution_y))
    preset = settings.resolution_preset or 'CUSTOM'

    if preset == 'ORIGINAL':
        return base_w, base_h, 100.0

    if preset == 'DOUBLE':
        return base_w * 2, base_h * 2, 200.0

    preset_h = RESOLUTION_PRESET_HEIGHTS.get(preset)
    if preset_h is not None:
        target_h = min(preset_h, base_h * 2)
        scale = target_h / base_h
        out_w = max(1, int(round(base_w * scale)))
        out_h = max(1, int(target_h))
        return out_w, out_h, scale * 100.0

    pct = max(1.0, min(200.0, float(settings.render_percentage)))
    out_w = max(1, int(round(base_w * pct / 100.0)))
    out_h = max(1, int(round(base_h * pct / 100.0)))
    return out_w, out_h, pct


def _get_rig_collection(scene, create=False):
    coll = bpy.data.collections.get(RIG_COLLECTION)
    if coll is None and create:
        coll = bpy.data.collections.new(RIG_COLLECTION)
        scene.collection.children.link(coll)
    elif coll is not None and create:
        # Ensure it is linked to this scene at least once.
        if scene.collection.children.get(coll.name) is None:
            try:
                scene.collection.children.link(coll)
            except RuntimeError:
                pass
    return coll


def _rig_object(name):
    return bpy.data.objects.get(name)


def _is_gs_camera(obj):
    return bool(obj and obj.type == 'CAMERA' and obj.get("gs_capture_camera", False))


def _rig_cameras():
    cams = [obj for obj in bpy.data.objects if _is_gs_camera(obj)]
    cams.sort(key=lambda o: int(o.get("gs_index", 999999)))
    return cams


def _render_target_cameras(settings):
    cams = _rig_cameras()
    if settings.render_camera_mode == 'CUSTOM':
        cams = [cam for cam in cams if getattr(cam, "gs_render_enabled", True)]
    return cams


def _set_all_render_camera_flags(enabled=True):
    for cam in _rig_cameras():
        try:
            cam.gs_render_enabled = bool(enabled)
        except Exception:
            pass


def _cleanup_orphan_generated_data():
    for data in list(bpy.data.cameras):
        if data.get("gs_capture_data", False) and data.users == 0:
            bpy.data.cameras.remove(data)
    for mesh in list(bpy.data.meshes):
        if mesh.get("gs_capture_data", False) and mesh.users == 0:
            bpy.data.meshes.remove(mesh)


def _delete_rig():
    coll = bpy.data.collections.get(RIG_COLLECTION)
    if coll:
        for obj in list(coll.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.collections.remove(coll)
    _cleanup_orphan_generated_data()


def _delete_generated_cameras_and_guide():
    coll = bpy.data.collections.get(RIG_COLLECTION)
    if not coll:
        return
    for obj in list(coll.objects):
        if _is_gs_camera(obj) or obj.name == GUIDE_NAME:
            bpy.data.objects.remove(obj, do_unlink=True)
    _cleanup_orphan_generated_data()


def _bounds_center_local(obj):
    if not hasattr(obj, "bound_box") or obj.bound_box is None:
        return Vector((0.0, 0.0, 0.0))
    corners = [Vector(c) for c in obj.bound_box]
    if not corners:
        return Vector((0.0, 0.0, 0.0))
    center = Vector((0.0, 0.0, 0.0))
    for c in corners:
        center += c
    return center / len(corners)


def _target_world_location(scene, settings):
    if settings.target_mode == 'CURSOR':
        return scene.cursor.location.copy()

    obj = settings.target_object
    if obj is None:
        return None

    if settings.target_mode == 'OBJECT_BOUNDS':
        return obj.matrix_world @ _bounds_center_local(obj)

    return obj.matrix_world.translation.copy()


def _configure_target_proxy(scene, settings, coll):
    old = bpy.data.objects.get(TARGET_NAME)
    if old:
        bpy.data.objects.remove(old, do_unlink=True)

    target = bpy.data.objects.new(TARGET_NAME, None)
    coll.objects.link(target)
    target.empty_display_type = 'SPHERE'
    target.empty_display_size = max(settings.radius * 0.06, 0.05)
    target.show_in_front = True
    target.hide_render = True
    target["gs_capture_helper"] = True

    if settings.target_mode == 'CURSOR':
        target.matrix_world.translation = scene.cursor.location.copy()
        return target

    obj = settings.target_object
    if obj is None:
        raise ValueError("Choose a target object or switch Target Mode to 3D Cursor.")

    target.parent = obj
    target.matrix_parent_inverse = Matrix.Identity(4)
    if settings.target_mode == 'OBJECT_BOUNDS':
        target.location = _bounds_center_local(obj)
    else:
        target.location = Vector((0.0, 0.0, 0.0))
    target.rotation_euler = (0.0, 0.0, 0.0)
    target.scale = (1.0, 1.0, 1.0)
    return target


def _reference_alignment(camera_obj, target_world):
    cam_pos = camera_obj.matrix_world.translation.copy()
    pole = cam_pos - target_world
    if pole.length < 1e-6:
        raise ValueError("Reference camera is at the target point; move it away first.")

    z_axis = pole.normalized()  # Local +Z of the rig points from target to reference camera.
    cam_basis = camera_obj.matrix_world.to_3x3().normalized()
    desired_up = (cam_basis @ Vector((0.0, 1.0, 0.0))).normalized()

    # Preserve the reference camera roll as much as possible while forcing the pole to aim at target.
    y_axis = desired_up - z_axis * desired_up.dot(z_axis)
    if y_axis.length < 1e-5:
        fallback = Vector((0.0, 0.0, 1.0))
        if abs(fallback.dot(z_axis)) > 0.98:
            fallback = Vector((0.0, 1.0, 0.0))
        y_axis = fallback - z_axis * fallback.dot(z_axis)
    y_axis.normalize()
    x_axis = y_axis.cross(z_axis).normalized()
    y_axis = z_axis.cross(x_axis).normalized()

    rot = Matrix((x_axis, y_axis, z_axis)).transposed().to_quaternion()

    cam_forward = (cam_basis @ Vector((0.0, 0.0, -1.0))).normalized()
    desired_forward = (target_world - cam_pos).normalized()
    try:
        aim_error = cam_forward.angle(desired_forward)
    except ValueError:
        aim_error = 0.0

    return rot, pole.length, aim_error


def _create_anchor_and_control(scene, settings, coll, target_proxy, base_rotation=None):
    anchor = bpy.data.objects.new(ANCHOR_NAME, None)
    coll.objects.link(anchor)
    anchor.empty_display_type = 'PLAIN_AXES'
    anchor.empty_display_size = max(settings.radius * 0.12, 0.1)
    anchor.show_in_front = True
    anchor.hide_render = True
    anchor.rotation_mode = 'QUATERNION'
    anchor.location = target_proxy.matrix_world.translation.copy()
    anchor.rotation_quaternion = base_rotation if base_rotation else Matrix.Identity(3).to_quaternion()
    anchor["gs_capture_helper"] = True

    copy_loc = anchor.constraints.new(type='COPY_LOCATION')
    copy_loc.name = "GS Follow Target"
    copy_loc.target = target_proxy
    copy_loc.target_space = 'WORLD'
    copy_loc.owner_space = 'WORLD'

    control = bpy.data.objects.new(CONTROL_NAME, None)
    coll.objects.link(control)
    control.empty_display_type = 'CIRCLE'
    control.empty_display_size = max(settings.radius * 0.25, 0.15)
    control.show_in_front = True
    control.hide_render = True
    control.parent = anchor
    control.location = (0.0, 0.0, 0.0)
    control.rotation_mode = 'XYZ'
    control.rotation_euler = (0.0, 0.0, 0.0)
    control.scale = (1.0, 1.0, 1.0)
    control["gs_capture_helper"] = True
    return anchor, control


def _dome26_points(radius, cap_angle):
    # Legacy physical-rig style distribution: 1 + 5 + 8 + 12 = 26.
    rings = (
        (1, 0.0, 0.0),
        (5, cap_angle / 3.0, 0.0),
        (8, cap_angle * 2.0 / 3.0, math.pi / 8.0),
        (12, cap_angle, math.pi / 12.0),
    )
    points = []
    for count, theta, offset in rings:
        if count == 1:
            points.append(Vector((0.0, 0.0, radius)))
            continue
        st = math.sin(theta)
        ct = math.cos(theta)
        for i in range(count):
            phi = offset + (2.0 * math.pi * i / count)
            points.append(Vector((
                radius * st * math.cos(phi),
                radius * st * math.sin(phi),
                radius * ct,
            )))
    return points


def _adaptive_ring_layout(cap_angle, ring_count, density):
    """Return [(count, theta, phase), ...] for near-even angular spacing.

    ``ring_count`` excludes the pole. Camera count on each latitude grows with
    sin(theta), so the surface sampling stays much more uniform than a fixed
    cameras-per-ring scheme. ``density`` is a simple global multiplier.
    """
    ring_count = max(1, int(ring_count))
    density = max(0.1, float(density))
    step = cap_angle / ring_count
    layout = []
    for ring_idx in range(1, ring_count + 1):
        theta = step * ring_idx
        # Circumference of the latitude circle divided by the meridional step.
        ideal = density * (2.0 * math.pi * max(1e-6, math.sin(theta)) / max(step, 1e-6))
        count = max(3, int(round(ideal)))
        # Half-step stagger on alternating rings prevents vertical camera columns.
        phase = (math.pi / count) if (ring_idx % 2 == 0) else 0.0
        layout.append((count, theta, phase))
    return layout


def _adaptive_ring_points(radius, cap_angle, ring_count, density):
    points = [Vector((0.0, 0.0, radius))]  # pole camera
    for count, theta, phase in _adaptive_ring_layout(cap_angle, ring_count, density):
        st = math.sin(theta)
        ct = math.cos(theta)
        for i in range(count):
            phi = phase + (2.0 * math.pi * i / count)
            points.append(Vector((
                radius * st * math.cos(phi),
                radius * st * math.sin(phi),
                radius * ct,
            )))
    return points


def _fibonacci_points(radius, cap_angle, count):
    if count <= 1:
        return [Vector((0.0, 0.0, radius))]

    points = []
    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    cos_cap = math.cos(cap_angle)
    for i in range(count):
        # Equal-area progression across the spherical cap. Includes pole and cap edge.
        u = i / (count - 1)
        cos_theta = 1.0 - u * (1.0 - cos_cap)
        cos_theta = max(-1.0, min(1.0, cos_theta))
        theta = math.acos(cos_theta)
        phi = 0.0 if i == 0 else (i - 1) * golden_angle
        st = math.sin(theta)
        points.append(Vector((
            radius * st * math.cos(phi),
            radius * st * math.sin(phi),
            radius * math.cos(theta),
        )))
    return points


def _capture_points(settings):
    if settings.distribution == 'DOME26':
        return _dome26_points(settings.radius, settings.cap_angle)
    if settings.distribution == 'RINGS':
        return _adaptive_ring_points(
            settings.radius,
            settings.cap_angle,
            settings.ring_count,
            settings.ring_density,
        )
    return _fibonacci_points(settings.radius, settings.cap_angle, settings.camera_count)


def _lookaround_directions(count):
    """Uniform full-sphere viewing directions with view 0 matching camera forward.

    Directions are expressed in the Reference Camera's local frame. The first
    direction is local -Z, so GS_CAM_000 exactly matches the reference view.
    Remaining views use a spherical Fibonacci distribution over 360 x 180 deg.
    """
    count = max(1, int(count))
    if count == 1:
        return [Vector((0.0, 0.0, -1.0))]

    golden_angle = math.pi * (3.0 - math.sqrt(5.0))
    directions = []
    for i in range(count):
        u = i / (count - 1)
        z = 1.0 - 2.0 * u
        r = math.sqrt(max(0.0, 1.0 - z * z))
        phi = 0.0 if i == 0 else (i - 1) * golden_angle
        # Spherical +Z is remapped to camera-local -Z so the first generated
        # camera is an exact orientation match to the Reference Camera.
        directions.append(Vector((
            r * math.cos(phi),
            r * math.sin(phi),
            -z,
        )).normalized())
    return directions


def _create_lookaround_anchor_and_control(coll, reference_camera):
    """Create a rig whose origin and basis follow the Reference Camera."""
    anchor = bpy.data.objects.new(ANCHOR_NAME, None)
    coll.objects.link(anchor)
    anchor.empty_display_type = 'PLAIN_AXES'
    anchor.empty_display_size = 0.35
    anchor.show_in_front = True
    anchor.hide_render = True
    anchor.rotation_mode = 'QUATERNION'
    anchor.matrix_world = reference_camera.matrix_world.copy()
    anchor["gs_capture_helper"] = True

    follow = anchor.constraints.new(type='COPY_TRANSFORMS')
    follow.name = "GS Follow Reference Camera"
    follow.target = reference_camera

    control = bpy.data.objects.new(CONTROL_NAME, None)
    coll.objects.link(control)
    control.empty_display_type = 'CIRCLE'
    control.empty_display_size = 0.6
    control.show_in_front = True
    control.hide_render = True
    control.parent = anchor
    control.location = (0.0, 0.0, 0.0)
    control.rotation_mode = 'XYZ'
    control.rotation_euler = (0.0, 0.0, 0.0)
    control.scale = (1.0, 1.0, 1.0)
    control["gs_capture_helper"] = True
    return anchor, control


def _create_lookaround_guide(coll, control, directions, length=2.0):
    verts = [Vector((0.0, 0.0, 0.0))]
    edges = []
    for direction in directions:
        verts.append(direction * length)
        edges.append((0, len(verts) - 1))

    mesh = bpy.data.meshes.new("GS_LookAroundGuideMesh")
    mesh["gs_capture_data"] = True
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    guide = bpy.data.objects.new(GUIDE_NAME, mesh)
    coll.objects.link(guide)
    guide.parent = control
    guide.location = (0.0, 0.0, 0.0)
    guide.display_type = 'WIRE'
    guide.show_in_front = True
    guide.hide_render = True
    guide["gs_capture_helper"] = True
    return guide


def _create_lookaround_cameras(settings, coll, control, reference_camera):
    data = _get_or_create_camera_data(settings, reference_camera)
    directions = _lookaround_directions(settings.lookaround_camera_count)

    for source_index, direction in enumerate(directions):
        cam_obj = bpy.data.objects.new(f"{CAM_PREFIX}{source_index:03d}", data)
        coll.objects.link(cam_obj)
        cam_obj.parent = control
        cam_obj.location = (0.0, 0.0, 0.0)
        cam_obj.rotation_mode = 'QUATERNION'
        # Existing aim helper expects a point whose negative vector is the
        # desired camera-forward direction.
        cam_obj.rotation_quaternion = _camera_local_rotation(-direction)
        cam_obj["gs_capture_camera"] = True
        cam_obj["gs_index"] = source_index
        cam_obj["gs_pole_camera"] = (source_index == 0)
        cam_obj["gs_capture_approach"] = 'LOOK_AROUND'
        cam_obj.gs_render_enabled = True

    settings.last_collision_skipped = 0
    return len(directions), directions


def _build_lookaround_rig(context, reference_camera):
    scene = context.scene
    settings = scene.gs_capture
    if reference_camera is None or reference_camera.type != 'CAMERA' or _is_gs_camera(reference_camera):
        raise ValueError("Choose a valid Reference Camera for Fixed Position / Look Around mode.")

    if settings.copy_reference_camera_settings:
        settings.lens = reference_camera.data.lens
        settings.sensor_width = reference_camera.data.sensor_width

    _delete_rig()
    coll = _get_rig_collection(scene, create=True)
    _anchor, control = _create_lookaround_anchor_and_control(coll, reference_camera)
    count, directions = _create_lookaround_cameras(settings, coll, control, reference_camera)
    if settings.show_dome_guide:
        _create_lookaround_guide(coll, control, directions)
    context.view_layer.update()
    return count, 0.0


def _rebuild_lookaround_existing(context):
    scene = context.scene
    settings = scene.gs_capture
    reference = settings.reference_camera
    if reference is None or reference.type != 'CAMERA' or _is_gs_camera(reference):
        raise ValueError("Choose a valid Reference Camera for Fixed Position / Look Around mode.")

    coll = _get_rig_collection(scene, create=False)
    anchor = _rig_object(ANCHOR_NAME)
    control = _rig_object(CONTROL_NAME)
    if not coll or not anchor or not control:
        raise ValueError("No GS Capture rig exists. Build one first.")

    old_control_rot = control.rotation_euler.copy()
    old_render_flags = {
        int(cam.get("gs_index", -1)): bool(getattr(cam, "gs_render_enabled", True))
        for cam in _rig_cameras()
    }

    _delete_generated_cameras_and_guide()

    follow = anchor.constraints.get("GS Follow Reference Camera")
    if follow is None or follow.type != 'COPY_TRANSFORMS':
        for constraint in list(anchor.constraints):
            anchor.constraints.remove(constraint)
        follow = anchor.constraints.new(type='COPY_TRANSFORMS')
        follow.name = "GS Follow Reference Camera"
    follow.target = reference
    anchor.matrix_world = reference.matrix_world.copy()
    control.rotation_euler = old_control_rot

    count, directions = _create_lookaround_cameras(settings, coll, control, reference)
    for cam in _rig_cameras():
        idx = int(cam.get("gs_index", -1))
        if idx in old_render_flags:
            cam.gs_render_enabled = old_render_flags[idx]
    if settings.show_dome_guide:
        _create_lookaround_guide(coll, control, directions)
    context.view_layer.update()
    return count


def _guide_ring_angles(settings):
    if settings.distribution == 'RINGS':
        return [theta for _count, theta, _phase in _adaptive_ring_layout(
            settings.cap_angle, settings.ring_count, settings.ring_density
        )]
    if settings.distribution == 'DOME26':
        return [settings.cap_angle / 3.0, settings.cap_angle * 2.0 / 3.0, settings.cap_angle]
    # Fibonacci has no real latitude rings; show three reference latitudes.
    return [settings.cap_angle / 3.0, settings.cap_angle * 2.0 / 3.0, settings.cap_angle]


def _get_or_create_camera_data(settings, reference_camera=None):
    """Return the shared GS camera datablock.

    Reference-camera copying is used as a template for secondary camera settings
    (clip planes, DOF, camera type, etc.), but the explicit GS Lens / Sensor Width
    values always win. This is important for Framing / Scale: a regenerate must not
    silently restore the reference camera's original focal length.
    """
    data = bpy.data.cameras.get(CAM_DATA_NAME)
    if data is None:
        if reference_camera is not None and settings.copy_reference_camera_settings:
            data = reference_camera.data.copy()
            data.name = CAM_DATA_NAME
        else:
            data = bpy.data.cameras.new(CAM_DATA_NAME)

    # These are the authoritative optics for the generated dome. Build From
    # Reference Camera first syncs settings.lens / sensor_width from the source,
    # while Framing / Scale deliberately changes them afterward.
    data.lens = settings.lens
    data.sensor_width = settings.sensor_width
    data["gs_capture_data"] = True
    return data


def _create_dome_guide(coll, control, settings):
    radius = settings.radius
    cap_angle = settings.cap_angle
    verts = []
    edges = []

    def add_polyline(points, closed=False):
        start = len(verts)
        verts.extend(points)
        for i in range(len(points) - 1):
            edges.append((start + i, start + i + 1))
        if closed and len(points) > 2:
            edges.append((start + len(points) - 1, start))

    segments = 64
    for theta in _guide_ring_angles(settings):
        ring = []
        st = math.sin(theta)
        ct = math.cos(theta)
        for i in range(segments):
            phi = 2.0 * math.pi * i / segments
            ring.append(Vector((
                radius * st * math.cos(phi),
                radius * st * math.sin(phi),
                radius * ct,
            )))
        add_polyline(ring, closed=True)

    meridians = 12
    arc_steps = 24
    for m in range(meridians):
        phi = 2.0 * math.pi * m / meridians
        arc = []
        for j in range(arc_steps + 1):
            theta = cap_angle * j / arc_steps
            arc.append(Vector((
                radius * math.sin(theta) * math.cos(phi),
                radius * math.sin(theta) * math.sin(phi),
                radius * math.cos(theta),
            )))
        add_polyline(arc, closed=False)

    mesh = bpy.data.meshes.new("GS_DomeGuideMesh")
    mesh["gs_capture_data"] = True
    mesh.from_pydata(verts, edges, [])
    mesh.update()
    guide = bpy.data.objects.new(GUIDE_NAME, mesh)
    coll.objects.link(guide)
    guide.parent = control
    guide.location = (0.0, 0.0, 0.0)
    guide.rotation_euler = (0.0, 0.0, 0.0)
    guide.display_type = 'WIRE'
    guide.show_in_front = True
    guide.hide_render = True
    guide["gs_capture_helper"] = True
    return guide


def _camera_local_rotation(point, up_hint_local=Vector((0.0, 1.0, 0.0))):
    """
    Aim Blender camera local -Z at the rig origin while preserving a consistent
    roll / horizon derived from the reference camera.

    The camera's local +Y is computed by projecting ``up_hint_local`` onto the
    image plane of each generated camera. This keeps the whole dome aligned to
    the same horizon instead of allowing per-camera flips. The default
    ``up_hint_local`` is rig +Y, which becomes the reference-camera's up vector
    when the dome is built from a reference camera.
    """
    if point.length < 1e-8:
        return Matrix.Identity(3).to_quaternion()

    z_back = point.normalized()  # Camera local +Z; local -Z therefore looks at origin.

    # Keep all cameras aligned to the same horizon by projecting a shared up
    # guide onto each camera's view plane.
    up = up_hint_local - z_back * up_hint_local.dot(z_back)
    if up.length < 1e-6:
        for fallback in (Vector((0.0, 0.0, 1.0)), Vector((1.0, 0.0, 0.0)), Vector((0.0, 1.0, 0.0))):
            up = fallback - z_back * fallback.dot(z_back)
            if up.length >= 1e-6:
                break
    up.normalize()

    right = up.cross(z_back)
    if right.length < 1e-6:
        right = Vector((1.0, 0.0, 0.0))
    right.normalize()
    up = z_back.cross(right).normalized()
    return Matrix((right, up, z_back)).transposed().to_quaternion()


def _create_cameras(context, settings, coll, control, target_proxy, reference_camera=None):
    data = _get_or_create_camera_data(settings, reference_camera)
    points = _capture_points(settings)
    up_hint_local = Vector((0.0, 1.0, 0.0))
    created = 0
    skipped = 0

    context.view_layer.update()
    for source_index, point in enumerate(points):
        if not _camera_point_allowed(context, settings, control, point):
            skipped += 1
            continue

        cam_obj = bpy.data.objects.new(f"{CAM_PREFIX}{source_index:03d}", data)
        coll.objects.link(cam_obj)
        cam_obj.parent = control
        cam_obj.location = point
        cam_obj.rotation_mode = 'QUATERNION'
        cam_obj.rotation_quaternion = _camera_local_rotation(point, up_hint_local=up_hint_local)
        cam_obj["gs_capture_camera"] = True
        cam_obj["gs_index"] = source_index
        cam_obj["gs_pole_camera"] = (source_index == 0)
        cam_obj.gs_render_enabled = True

        track = cam_obj.constraints.new(type='DAMPED_TRACK')
        track.name = "GS Aim Target"
        track.target = target_proxy
        track.track_axis = 'TRACK_NEGATIVE_Z'
        created += 1

    settings.last_collision_skipped = skipped
    return created


def _build_rig(context, reference_camera=None):
    scene = context.scene
    settings = scene.gs_capture

    if settings.capture_approach == 'LOOK_AROUND':
        reference_camera = reference_camera or _find_reference_camera(context)
        return _build_lookaround_rig(context, reference_camera)

    target_world = _target_world_location(scene, settings)
    if target_world is None:
        raise ValueError("Choose a target object or use 3D Cursor target mode.")

    base_rotation = None
    aim_error = 0.0
    if reference_camera is not None:
        base_rotation, radius, aim_error = _reference_alignment(reference_camera, target_world)
        settings.radius = radius
        if settings.copy_reference_camera_settings:
            settings.lens = reference_camera.data.lens
            settings.sensor_width = reference_camera.data.sensor_width
        settings.framing_reference_distance = radius
        settings.framing_reference_lens = reference_camera.data.lens
        settings.framing_reference_sensor = reference_camera.data.sensor_width
        settings.framing_scale = 1.0

    _delete_rig()
    coll = _get_rig_collection(scene, create=True)
    target_proxy = _configure_target_proxy(scene, settings, coll)
    context.view_layer.update()
    anchor, control = _create_anchor_and_control(
        scene, settings, coll, target_proxy, base_rotation=base_rotation
    )
    count = _create_cameras(context, settings, coll, control, target_proxy, reference_camera)
    if settings.show_dome_guide:
        _create_dome_guide(coll, control, settings)

    context.view_layer.update()
    return count, aim_error


def _rebuild_existing(context):
    scene = context.scene
    settings = scene.gs_capture
    if settings.capture_approach == 'LOOK_AROUND':
        return _rebuild_lookaround_existing(context)

    coll = _get_rig_collection(scene, create=False)
    anchor = _rig_object(ANCHOR_NAME)
    control = _rig_object(CONTROL_NAME)
    target_proxy = _rig_object(TARGET_NAME)
    if not coll or not anchor or not control or not target_proxy:
        raise ValueError("No GS Capture rig exists. Build one first.")

    # Reconfigure target without destroying the rig orientation.
    old_control_rot = control.rotation_euler.copy()
    old_anchor_rot = anchor.rotation_quaternion.copy()
    old_render_flags = {
        int(cam.get("gs_index", -1)): bool(getattr(cam, "gs_render_enabled", True))
        for cam in _rig_cameras()
    }

    _delete_generated_cameras_and_guide()
    bpy.data.objects.remove(target_proxy, do_unlink=True)
    target_proxy = _configure_target_proxy(scene, settings, coll)
    context.view_layer.update()

    # Retarget anchor's Copy Location constraint.
    follow = anchor.constraints.get("GS Follow Target")
    if follow is None:
        follow = anchor.constraints.new(type='COPY_LOCATION')
        follow.name = "GS Follow Target"
    follow.target = target_proxy
    follow.target_space = 'WORLD'
    follow.owner_space = 'WORLD'
    anchor.rotation_quaternion = old_anchor_rot
    control.rotation_euler = old_control_rot

    reference = settings.reference_camera
    if reference is not None and (reference.type != 'CAMERA' or _is_gs_camera(reference)):
        reference = None
    count = _create_cameras(context, settings, coll, control, target_proxy, reference_camera=reference)
    for cam in _rig_cameras():
        idx = int(cam.get("gs_index", -1))
        if idx in old_render_flags:
            cam.gs_render_enabled = old_render_flags[idx]
    if settings.show_dome_guide:
        _create_dome_guide(coll, control, settings)
    context.view_layer.update()
    return count


def _find_reference_camera(context):
    settings = context.scene.gs_capture

    obj = settings.reference_camera
    if obj and obj.type == 'CAMERA' and not _is_gs_camera(obj):
        return obj

    # Backward-compatible fallback: if no camera is assigned in the selector,
    # use the active scene camera and store it in the selector.
    obj = context.view_layer.objects.active
    if obj and obj.type == 'CAMERA' and not _is_gs_camera(obj):
        settings.reference_camera = obj
        return obj
    return None


def _infer_target_from_selection(context, reference_camera):
    settings = context.scene.gs_capture
    if settings.target_mode == 'CURSOR' or settings.target_object is not None:
        return
    candidates = [
        obj for obj in context.selected_objects
        if obj != reference_camera and obj.type != 'CAMERA' and not obj.get("gs_capture_helper", False)
    ]
    if len(candidates) == 1:
        settings.target_object = candidates[0]


def _select_only(context, obj):
    for selected in list(context.selected_objects):
        selected.select_set(False)
    obj.select_set(True)
    context.view_layer.objects.active = obj


def _render_extension(file_format):
    return {
        'PNG': '.png',
        'JPEG': '.jpg',
        'OPEN_EXR': '.exr',
    }.get(file_format, '.png')


def _camera_intrinsics(scene, cam_obj):
    scale = scene.render.resolution_percentage / 100.0
    w = int(round(scene.render.resolution_x * scale))
    h = int(round(scene.render.resolution_y * scale))
    data = cam_obj.data
    fl_x = 0.5 * w / math.tan(data.angle_x * 0.5)
    fl_y = 0.5 * h / math.tan(data.angle_y * 0.5)

    # For normal unshifted cameras this is exact. Shift is included as a practical approximation.
    cx = w * (0.5 - data.shift_x)
    cy = h * (0.5 + data.shift_y)

    return {
        "w": w,
        "h": h,
        "fl_x": fl_x,
        "fl_y": fl_y,
        "cx": cx,
        "cy": cy,
        "camera_angle_x": float(data.angle_x),
        "camera_angle_y": float(data.angle_y),
    }


def _evaluated_matrix_world(context, obj):
    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = obj.evaluated_get(depsgraph)
    return obj_eval.matrix_world.copy()


def _matrix_to_list(matrix):
    return [[float(v) for v in row] for row in matrix]


def _reference_camera_poll(self, obj):
    return bool(obj and obj.type == 'CAMERA' and not _is_gs_camera(obj))

def _apply_reference_camera_settings(settings, camera_obj):
    if camera_obj is None or camera_obj.type != 'CAMERA' or _is_gs_camera(camera_obj):
        return
    cam_data = camera_obj.data
    settings.lens = cam_data.lens
    settings.sensor_width = cam_data.sensor_width


def _reference_camera_update(self, context):
    _apply_reference_camera_settings(self, self.reference_camera)


def _mesh_object_poll(self, obj):
    return bool(obj and obj.type == 'MESH' and not obj.get("gs_capture_helper", False))


def _world_bbox_z_range(obj):
    try:
        corners = [obj.matrix_world @ Vector(corner) for corner in obj.bound_box]
        if corners:
            zs = [p.z for p in corners]
            return min(zs), max(zs)
    except Exception:
        pass
    z = obj.matrix_world.translation.z
    return z - 1.0, z + 1.0


def _ground_surface_hit(context, surface_obj, world_point):
    """Find the selected terrain/ground height at a camera candidate XY."""
    if surface_obj is None or surface_obj.type != 'MESH':
        return None
    depsgraph = context.evaluated_depsgraph_get()
    obj_eval = surface_obj.evaluated_get(depsgraph)
    _zmin, zmax = _world_bbox_z_range(obj_eval)
    start_world = Vector((world_point.x, world_point.y, zmax + max(10.0, abs(zmax) * 0.05 + 1.0)))
    inv = obj_eval.matrix_world.inverted_safe()
    origin_local = inv @ start_world
    direction_local = inv.to_3x3() @ Vector((0.0, 0.0, -1.0))
    if direction_local.length < 1e-8:
        return None
    direction_local.normalize()
    try:
        hit, location, _normal, _index = obj_eval.ray_cast(origin_local, direction_local, distance=1.0e7)
    except Exception:
        return None
    if not hit:
        return None
    return obj_eval.matrix_world @ location


def _camera_point_allowed(context, settings, control, local_point):
    if not settings.collision_enabled or settings.collision_object is None:
        return True
    world_point = control.matrix_world @ local_point
    hit = _ground_surface_hit(context, settings.collision_object, world_point)
    if hit is None:
        return True
    return world_point.z >= (hit.z + settings.collision_clearance)


def _capture_framing_reference(scene, settings, camera_obj):
    target_world = _target_world_location(scene, settings)
    if target_world is None or camera_obj is None or camera_obj.type != 'CAMERA':
        raise ValueError("Choose both a target and a valid Reference Camera first.")
    distance = (camera_obj.matrix_world.translation - target_world).length
    if distance <= 1e-6:
        raise ValueError("Reference Camera is at the target point.")
    settings.framing_reference_distance = distance
    settings.framing_reference_lens = camera_obj.data.lens
    settings.framing_reference_sensor = camera_obj.data.sensor_width
    settings.framing_scale = settings.radius / distance if distance > 1e-6 else 1.0
    return distance


def _matched_lens_for_radius(settings, radius=None):
    radius = float(settings.radius if radius is None else radius)
    d0 = max(1e-6, float(settings.framing_reference_distance))
    f0 = max(1e-6, float(settings.framing_reference_lens))
    s0 = max(1e-6, float(settings.framing_reference_sensor))
    s1 = max(1e-6, float(settings.sensor_width))
    return f0 * (radius / d0) * (s1 / s0)


def _is_render_job_running():
    """Best-effort check for Blender's active render job."""
    try:
        fn = getattr(bpy.app, "is_job_running", None)
        if fn is not None:
            return bool(fn("RENDER"))
    except Exception:
        pass
    return False


def _store_render_restore_state(scene, settings):
    settings.restore_camera = scene.camera
    settings.restore_filepath = scene.render.filepath
    settings.restore_format = scene.render.image_settings.file_format
    settings.restore_use_ext = scene.render.use_file_extension
    settings.restore_resolution_x = scene.render.resolution_x
    settings.restore_resolution_y = scene.render.resolution_y
    settings.restore_resolution_percentage = scene.render.resolution_percentage
    settings.restore_use_compositing = bool(getattr(scene.render, "use_compositing", True))
    settings.restore_state_valid = True


def _restore_render_state_from_scene(scene, settings):
    """Restore render overrides even if the modal queue died or lost its context."""
    if not getattr(settings, "restore_state_valid", False):
        return
    try:
        scene.camera = settings.restore_camera
        scene.render.filepath = settings.restore_filepath
        if settings.restore_format:
            scene.render.image_settings.file_format = settings.restore_format
        scene.render.use_file_extension = settings.restore_use_ext
        scene.render.resolution_x = settings.restore_resolution_x
        scene.render.resolution_y = settings.restore_resolution_y
        scene.render.resolution_percentage = settings.restore_resolution_percentage
        if hasattr(scene.render, "use_compositing"):
            scene.render.use_compositing = settings.restore_use_compositing
    finally:
        settings.restore_state_valid = False


def _schedule_stop_watchdog(scene_name):
    """Guarantee that a stopped queue resets even if Blender drops modal events."""
    def watchdog():
        scene = bpy.data.scenes.get(scene_name)
        if scene is None or not hasattr(scene, "gs_capture"):
            return None
        settings = scene.gs_capture
        if not settings.render_stop_requested:
            return None
        if _is_render_job_running():
            return 0.20

        _restore_render_state_from_scene(scene, settings)
        settings.render_in_progress = False
        settings.render_stop_requested = False
        settings.render_status = "Render stopped"
        # Keep the last known percentage so the user can see where it stopped.
        try:
            for window in bpy.context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
        except Exception:
            pass
        return None

    try:
        bpy.app.timers.register(watchdog, first_interval=0.20)
    except Exception:
        pass


class GSCaptureSettings(PropertyGroup):
    capture_approach: EnumProperty(
        name="Capture Approach",
        description="Choose between cameras orbiting a target or many orientations from one fixed camera position",
        items=(
            ('ORBIT', "Orbit Target / Sphere", "Default: cameras are distributed around the target and look inward"),
            ('LOOK_AROUND', "Fixed Position / Look Around", "All generated cameras share the Reference Camera position and look in different directions"),
        ),
        default='ORBIT',
    )
    lookaround_camera_count: IntProperty(
        name="View Count",
        description="Number of viewing directions generated from the fixed Reference Camera position",
        default=26,
        min=2,
        max=512,
    )
    target_mode: EnumProperty(
        name="Target Mode",
        items=(
            ('OBJECT_ORIGIN', "Object Origin", "Aim at and center the rig on the target object's origin"),
            ('OBJECT_BOUNDS', "Object Bounds Center", "Aim at the center of the target object's bounding box"),
            ('CURSOR', "3D Cursor", "Use the 3D Cursor as a free target point"),
        ),
        default='OBJECT_BOUNDS',
    )
    target_object: PointerProperty(
        name="Target Object",
        type=bpy.types.Object,
        description="Object the capture dome is centered on",
    )
    reference_camera: PointerProperty(
        name="Reference Camera",
        type=bpy.types.Object,
        poll=_reference_camera_poll,
        description="Existing camera used as the pole/center view of the capture dome",
        update=_reference_camera_update,
    )
    distribution: EnumProperty(
        name="Distribution",
        items=(
            ('RINGS', "Adaptive Rings", "Pole plus latitude rings; camera count per ring grows automatically for even coverage"),
            ('DOME26', "Legacy 26-Camera Dome", "Physical-rig style 1 + 5 + 8 + 12 hemisphere"),
            ('FIBONACCI', "Exact Camera Count", "Even spherical-cap distribution using an exact requested camera count"),
        ),
        default='RINGS',
    )
    ring_count: IntProperty(
        name="Ring Count",
        description="Number of latitude rings, excluding the pole camera",
        default=3,
        min=1,
        max=24,
    )
    ring_density: FloatProperty(
        name="Density Multiplier",
        description="Multiplies the mathematically estimated cameras per ring; 1.0 is near-equal angular spacing",
        default=0.90,
        min=0.20,
        max=4.0,
        soft_max=2.0,
    )
    camera_count: IntProperty(
        name="Camera Count",
        default=26,
        min=3,
        max=512,
    )
    radius: FloatProperty(
        name="Radius",
        description="Distance from target to cameras",
        default=3.0,
        min=0.01,
        soft_max=100.0,
        unit='LENGTH',
    )
    cap_angle: FloatProperty(
        name="Dome Angle",
        description="Angle from the pole camera to the outermost ring; 90 degrees is a full hemisphere",
        default=math.radians(90.0),
        min=math.radians(10.0),
        max=math.radians(90.0),
        subtype='ANGLE',
    )
    lens: FloatProperty(
        name="Lens",
        default=50.0,
        min=1.0,
        max=500.0,
        unit='CAMERA',
    )
    sensor_width: FloatProperty(
        name="Sensor Width",
        default=36.0,
        min=1.0,
        max=100.0,
    )
    copy_reference_camera_settings: BoolProperty(
        name="Copy Reference Camera Settings",
        description="When building from an active camera, copy its camera data (lens, sensor, clipping, DOF, etc.) to the shared GS camera data",
        default=True,
    )
    show_dome_guide: BoolProperty(
        name="Show Dome Guide",
        default=True,
    )
    collision_enabled: BoolProperty(
        name="Enable Ground Collision",
        description="Skip candidate cameras that are on or below the selected terrain/ground surface",
        default=False,
    )
    collision_object: PointerProperty(
        name="Ground / Collision Surface",
        type=bpy.types.Object,
        poll=_mesh_object_poll,
        description="Mesh used as terrain/ground. Evaluated modifiers and Geometry Nodes are included",
    )
    collision_clearance: FloatProperty(
        name="Minimum Clearance",
        description="Required vertical distance above the collision surface",
        default=0.20,
        min=0.0,
        soft_max=5.0,
        unit='LENGTH',
    )
    last_collision_skipped: IntProperty(
        name="Skipped Cameras",
        default=0,
        min=0,
        options={'SKIP_SAVE'},
    )
    framing_reference_distance: FloatProperty(
        name="Reference Distance",
        default=0.0,
        min=0.0,
        unit='LENGTH',
    )
    framing_reference_lens: FloatProperty(
        name="Reference Lens",
        default=50.0,
        min=0.1,
        unit='CAMERA',
    )
    framing_reference_sensor: FloatProperty(
        name="Reference Sensor",
        default=36.0,
        min=0.1,
    )
    framing_scale: FloatProperty(
        name="Rig Scale",
        description="Scale camera distance while changing focal length to preserve target-plane framing",
        default=1.0,
        min=0.05,
        max=20.0,
        soft_min=0.25,
        soft_max=4.0,
    )
    output_dir: StringProperty(
        name="Output Folder",
        subtype='DIR_PATH',
        default="//gs_capture/",
    )
    file_format: EnumProperty(
        name="Image Format",
        items=(
            ('PNG', "PNG", "Lossless RGB capture"),
            ('JPEG', "JPEG", "Smaller files; lossy"),
            ('OPEN_EXR', "OpenEXR", "High dynamic range capture"),
        ),
        default='PNG',
    )
    resolution_preset: EnumProperty(
        name="Resolution Preset",
        description="Choose a standard output size. Presets preserve the current scene aspect ratio and never exceed 2x the base resolution",
        items=_resolution_preset_items,
    )
    render_percentage: IntProperty(
        name="Render Resolution",
        description="Custom capture resolution as a percentage of the scene's base resolution; maximum is 2x",
        default=100,
        min=1,
        max=200,
        subtype='PERCENTAGE',
    )
    use_compositor: BoolProperty(
        name="Apply Compositor / Post FX",
        description="Use the scene compositor while rendering GS views. Disable for raw renders without compositor post-processing",
        default=True,
    )
    export_transforms: BoolProperty(
        name="Write transforms.json",
        description="Write a Nerfstudio/OpenGL-style camera-to-world manifest next to the rendered views",
        default=True,
    )
    render_camera_mode: EnumProperty(
        name="Render Cameras",
        description="Choose whether to render all dome cameras or only specifically enabled ones",
        items=(
            ('ALL', "All Cameras", "Render every GS camera in the rig"),
            ('CUSTOM', "Selected Cameras", "Render only cameras enabled in the list below"),
        ),
        default='ALL',
    )
    restore_state_valid: BoolProperty(default=False, options={'SKIP_SAVE'})
    restore_camera: PointerProperty(type=bpy.types.Object, options={'SKIP_SAVE'})
    restore_filepath: StringProperty(default="", options={'SKIP_SAVE'})
    restore_format: StringProperty(default="", options={'SKIP_SAVE'})
    restore_use_ext: BoolProperty(default=True, options={'SKIP_SAVE'})
    restore_resolution_x: IntProperty(default=1920, min=1, options={'SKIP_SAVE'})
    restore_resolution_y: IntProperty(default=1080, min=1, options={'SKIP_SAVE'})
    restore_resolution_percentage: IntProperty(default=100, min=1, max=10000, options={'SKIP_SAVE'})
    restore_use_compositing: BoolProperty(default=True, options={'SKIP_SAVE'})
    render_in_progress: BoolProperty(
        name="Render In Progress",
        default=False,
        options={'SKIP_SAVE'},
    )
    render_stop_requested: BoolProperty(
        name="Render Stop Requested",
        default=False,
        options={'SKIP_SAVE'},
    )
    render_progress: FloatProperty(
        name="Render Progress",
        default=0.0,
        min=0.0,
        max=1.0,
        options={'SKIP_SAVE'},
    )
    render_status: StringProperty(
        name="Render Status",
        default="",
        options={'SKIP_SAVE'},
    )


class GS_OT_build_rig(Operator):
    bl_idname = "gs_capture.build_rig"
    bl_label = "Build Capture Dome"
    bl_description = "Create a hemispherical GS capture rig around the chosen target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            count, _ = _build_rig(context, reference_camera=None)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        skipped = context.scene.gs_capture.last_collision_skipped
        suffix = f"; skipped {skipped} collision cameras" if skipped else ""
        self.report({'INFO'}, f"Built GS Capture rig with {count} cameras{suffix}")
        return {'FINISHED'}


class GS_OT_build_from_active_camera(Operator):
    bl_idname = "gs_capture.build_from_active_camera"
    bl_label = "Build From Reference Camera"
    bl_description = "Use the selected Reference Camera as the pole/center view of the dome, preserving its distance and roll as closely as possible"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        reference = _find_reference_camera(context)
        if reference is None:
            self.report({'ERROR'}, "Choose a Reference Camera, or select an existing non-GS camera")
            return {'CANCELLED'}

        _apply_reference_camera_settings(context.scene.gs_capture, reference)
        if context.scene.gs_capture.capture_approach == 'ORBIT':
            _infer_target_from_selection(context, reference)
        try:
            count, aim_error = _build_rig(context, reference_camera=reference)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        deg = math.degrees(aim_error)
        if deg > 0.25:
            self.report(
                {'WARNING'},
                f"Built {count} cameras. Reference camera was {deg:.2f} degrees off-target, so the dome pole was re-aimed at the target."
            )
        else:
            skipped = context.scene.gs_capture.last_collision_skipped
            suffix = f"; skipped {skipped} collision cameras" if skipped else ""
            self.report({'INFO'}, f"Built {count} cameras from reference camera{suffix}")
        return {'FINISHED'}


class GS_OT_rebuild_rig(Operator):
    bl_idname = "gs_capture.rebuild_rig"
    bl_label = "Regenerate Dome"
    bl_description = "Regenerate cameras and guide from the current settings while preserving the rig's current orbit rotation"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        try:
            count = _rebuild_existing(context)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        skipped = context.scene.gs_capture.last_collision_skipped
        suffix = f"; skipped {skipped} collision cameras" if skipped else ""
        self.report({'INFO'}, f"Regenerated dome with {count} cameras{suffix}")
        return {'FINISHED'}


class GS_OT_delete_rig(Operator):
    bl_idname = "gs_capture.delete_rig"
    bl_label = "Delete Rig"
    bl_description = "Delete the generated GS Capture rig"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        _delete_rig()
        self.report({'INFO'}, "Deleted GS Capture rig")
        return {'FINISHED'}


class GS_OT_select_control(Operator):
    bl_idname = "gs_capture.select_control"
    bl_label = "Select Orbit Control"
    bl_description = "Select the rig control Empty so you can rotate the whole dome interactively"

    def execute(self, context):
        obj = _rig_object(CONTROL_NAME)
        if obj is None:
            self.report({'ERROR'}, "No rig control found")
            return {'CANCELLED'}
        _select_only(context, obj)
        return {'FINISHED'}


class GS_OT_select_target(Operator):
    bl_idname = "gs_capture.select_target"
    bl_label = "Select Aim Target"
    bl_description = "Select the generated GS target Empty"

    def execute(self, context):
        obj = _rig_object(TARGET_NAME)
        if obj is None:
            self.report({'ERROR'}, "No GS target found")
            return {'CANCELLED'}
        _select_only(context, obj)
        return {'FINISHED'}


class GS_OT_set_selected_target(Operator):
    bl_idname = "gs_capture.set_selected_target"
    bl_label = "Set Selected as Target"
    bl_description = "Use the active non-camera object as the rig target"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type == 'CAMERA' or obj.get("gs_capture_helper", False):
            self.report({'ERROR'}, "Select a non-camera scene object")
            return {'CANCELLED'}
        context.scene.gs_capture.target_object = obj
        self.report({'INFO'}, f"Target set to {obj.name}")
        return {'FINISHED'}


class GS_OT_set_selected_reference_camera(Operator):
    bl_idname = "gs_capture.set_selected_reference_camera"
    bl_label = "Set Selected as Reference Camera"
    bl_description = "Use the active camera as the Reference Camera"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type != 'CAMERA' or _is_gs_camera(obj):
            self.report({'ERROR'}, "Select an existing non-GS camera")
            return {'CANCELLED'}
        settings = context.scene.gs_capture
        settings.reference_camera = obj
        _apply_reference_camera_settings(settings, obj)
        self.report({'INFO'}, f"Reference camera set to {obj.name}; optics refreshed")
        return {'FINISHED'}


class GS_OT_refresh_reference_camera_settings(Operator):
    bl_idname = "gs_capture.refresh_reference_camera_settings"
    bl_label = "Refresh Optics From Reference Camera"
    bl_description = "Refresh the displayed dome camera optics from the selected Reference Camera"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.gs_capture
        reference = settings.reference_camera
        if reference is None or reference.type != 'CAMERA' or _is_gs_camera(reference):
            self.report({'ERROR'}, "Choose a valid Reference Camera first")
            return {'CANCELLED'}
        _apply_reference_camera_settings(settings, reference)
        self.report({'INFO'}, f"Refreshed optics from {reference.name}")
        return {'FINISHED'}


class GS_OT_render_views(Operator):
    bl_idname = "gs_capture.render_views"
    bl_label = "Render All GS Views"
    bl_description = "Render every GS camera sequentially with a live, non-blocking progress queue"

    _timer = None
    _render_complete_handler = None
    _render_cancel_handler = None
    _render_watchdog_token = 0
    _max_interrupt_retries = 20

    def _tag_redraw(self, context):
        screen = getattr(context, "screen", None)
        if screen:
            for area in screen.areas:
                try:
                    area.tag_redraw()
                except Exception:
                    pass

    def _set_status(self, context, text, progress=None):
        settings = context.scene.gs_capture
        settings.render_status = text
        if progress is not None:
            settings.render_progress = max(0.0, min(1.0, progress))
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set(text=text if text else None)
            except Exception:
                pass
        self._tag_redraw(context)

    def _install_handlers(self):
        def on_complete(*args):
            self._render_finished = True
            self._render_cancelled = False

        def on_cancel(*args):
            self._render_finished = False
            self._render_cancelled = True

        self._render_complete_handler = on_complete
        self._render_cancel_handler = on_cancel
        bpy.app.handlers.render_complete.append(self._render_complete_handler)
        bpy.app.handlers.render_cancel.append(self._render_cancel_handler)

    def _remove_handlers(self):
        if self._render_complete_handler in bpy.app.handlers.render_complete:
            bpy.app.handlers.render_complete.remove(self._render_complete_handler)
        if self._render_cancel_handler in bpy.app.handlers.render_cancel:
            bpy.app.handlers.render_cancel.remove(self._render_cancel_handler)
        self._render_complete_handler = None
        self._render_cancel_handler = None

    def _schedule_render_watchdog(self, scene_name):
        """Fallback polling so the queue continues even if Blender drops a handler or UI event."""
        self._render_watchdog_token += 1
        token = self._render_watchdog_token

        def poll():
            if token != self._render_watchdog_token:
                return None
            scene = bpy.data.scenes.get(scene_name)
            if scene is None or not hasattr(scene, "gs_capture"):
                return None
            settings = scene.gs_capture
            if not settings.render_in_progress:
                return None
            if self._render_finished or self._render_cancelled:
                return None
            if _is_render_job_running():
                return 0.20

            # The render job ended, but one of Blender's handlers / modal events
            # did not advance the queue. Resolve it here so checked cameras keep
            # rendering even if the user interacts elsewhere in Blender.
            self._rendering = False
            if settings.render_stop_requested:
                self._render_cancelled = True
                print("[GS Capture] Watchdog detected stopped render")
            else:
                self._render_finished = True
                print("[GS Capture] Watchdog advanced completed render")
            return None

        try:
            bpy.app.timers.register(poll, first_interval=0.20)
        except Exception:
            pass

    def _retry_interrupted_render(self, context, reason="interrupted"):
        total = len(self._cameras)
        current = self._cameras[self._index]
        source_index = int(current.get("gs_index", self._index))
        retries = self._retry_counts.get(source_index, 0) + 1
        self._retry_counts[source_index] = retries

        if retries > self._max_interrupt_retries:
            self._cleanup(context, cancelled=True)
            self.report({'ERROR'}, f"Render for {current.name} kept stopping ({reason}). Aborted after {self._max_interrupt_retries} retries.")
            return {'CANCELLED'}

        self._render_finished = False
        self._render_cancelled = False
        self._rendering = False
        self._cancel_sent = False
        self._set_status(
            context,
            f"GS Capture: {reason}, retrying {self._index + 1}/{total} - {current.name} ({retries}/{self._max_interrupt_retries})",
            self._index / max(1, total),
        )
        print(f"[GS Capture] retry {retries}/{self._max_interrupt_retries} for {current.name} ({reason})")
        try:
            self._start_current_render(context)
        except Exception as exc:
            self._cleanup(context, cancelled=True)
            self.report({'ERROR'}, f"Could not restart render for {current.name}: {exc}")
            return {'CANCELLED'}
        return {'PASS_THROUGH'}

    def _restore_scene(self, context):
        scene = context.scene
        scene.camera = self._original_camera
        scene.render.filepath = self._original_filepath
        scene.render.image_settings.file_format = self._original_format
        scene.render.use_file_extension = self._original_use_ext
        scene.render.resolution_x = self._original_resolution_x
        scene.render.resolution_y = self._original_resolution_y
        scene.render.resolution_percentage = self._original_resolution_percentage
        if self._original_use_compositing is not None:
            scene.render.use_compositing = self._original_use_compositing

    def _cleanup(self, context, cancelled=False):
        scene = context.scene
        settings = scene.gs_capture
        wm = context.window_manager

        self._restore_scene(context)
        settings.restore_state_valid = False
        self._remove_handlers()
        self._render_watchdog_token += 1

        if self._timer is not None:
            try:
                wm.event_timer_remove(self._timer)
            except Exception:
                pass
            self._timer = None

        try:
            wm.progress_end()
        except Exception:
            pass

        settings.render_in_progress = False
        settings.render_stop_requested = False
        if cancelled:
            settings.render_status = "Render cancelled"
        else:
            settings.render_progress = 1.0
            settings.render_status = f"Finished {len(self._cameras)}/{len(self._cameras)} views"

        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set(text=None)
            except Exception:
                pass
        self._tag_redraw(context)

    def _write_manifest(self, context):
        settings = context.scene.gs_capture
        if not settings.export_transforms or self._intrinsics is None:
            return
        intrinsics = self._intrinsics
        manifest = {
            "camera_model": "OPENCV",
            "w": intrinsics["w"],
            "h": intrinsics["h"],
            "fl_x": intrinsics["fl_x"],
            "fl_y": intrinsics["fl_y"],
            "cx": intrinsics["cx"],
            "cy": intrinsics["cy"],
            "camera_angle_x": intrinsics["camera_angle_x"],
            "camera_angle_y": intrinsics["camera_angle_y"],
            "k1": 0.0,
            "k2": 0.0,
            "p1": 0.0,
            "p2": 0.0,
            "aabb_scale": 16,
            "blender_world_up": "Z",
            "frame_number": int(context.scene.frame_current),
            "frames": self._frames,
        }
        manifest_path = os.path.join(self._out_dir, "transforms.json")
        with open(manifest_path, "w", encoding="utf-8") as f:
            json.dump(manifest, f, indent=2)

    def _record_current_camera(self, context):
        settings = context.scene.gs_capture
        if not settings.export_transforms:
            return
        cam = self._cameras[self._index]
        if self._intrinsics is None:
            self._intrinsics = _camera_intrinsics(context.scene, cam)
        context.view_layer.update()
        mw = _evaluated_matrix_world(context, cam)
        source_index = int(cam.get("gs_index", self._index))
        stem = f"view_{source_index:03d}"
        self._frames.append({
            "file_path": f"./{stem}{self._ext}",
            "transform_matrix": _matrix_to_list(mw),
            "camera_name": cam.name,
        })

    def _start_current_render(self, context):
        scene = context.scene
        cam = self._cameras[self._index]
        total = len(self._cameras)
        step = self._index + 1

        scene.camera = cam
        context.view_layer.update()
        source_index = int(cam.get("gs_index", self._index))
        stem = f"view_{source_index:03d}"
        scene.render.filepath = os.path.join(self._out_dir, stem)

        self._render_finished = False
        self._render_cancelled = False
        self._cancel_sent = False
        self._rendering = True

        msg = f"GS Capture: rendering {step}/{total} - {cam.name}"
        self._set_status(context, msg, self._index / total)
        context.window_manager.progress_update(self._index)
        print(f"[GS Capture] {msg}")

        # INVOKE_DEFAULT is intentional: unlike the previous blocking EXEC loop,
        # this lets Blender keep its render UI / Render Result responsive.
        result = bpy.ops.render.render('INVOKE_DEFAULT', write_still=True)
        if 'CANCELLED' in result:
            self._rendering = False
            self._render_cancelled = True
        else:
            self._schedule_render_watchdog(scene.name)

    def invoke(self, context, event):
        scene = context.scene
        settings = scene.gs_capture

        if settings.render_in_progress:
            self.report({'WARNING'}, "A GS Capture render is already running")
            return {'CANCELLED'}

        self._cameras = _render_target_cameras(settings)
        if not self._cameras:
            if _rig_cameras():
                self.report({'ERROR'}, "No GS cameras are enabled for rendering")
            else:
                self.report({'ERROR'}, "No GS Capture cameras found")
            return {'CANCELLED'}

        self._out_dir = bpy.path.abspath(settings.output_dir)
        try:
            os.makedirs(self._out_dir, exist_ok=True)
        except OSError as exc:
            self.report({'ERROR'}, f"Cannot create output folder: {exc}")
            return {'CANCELLED'}

        _store_render_restore_state(scene, settings)

        self._original_camera = scene.camera
        self._original_filepath = scene.render.filepath
        self._original_format = scene.render.image_settings.file_format
        self._original_use_ext = scene.render.use_file_extension
        self._original_resolution_x = scene.render.resolution_x
        self._original_resolution_y = scene.render.resolution_y
        self._original_resolution_percentage = scene.render.resolution_percentage
        self._original_use_compositing = getattr(scene.render, "use_compositing", None)

        output_w, output_h, _ = _capture_output_dimensions(scene, settings)
        scene.render.image_settings.file_format = settings.file_format
        scene.render.use_file_extension = True
        scene.render.resolution_x = output_w
        scene.render.resolution_y = output_h
        scene.render.resolution_percentage = 100
        if self._original_use_compositing is not None:
            scene.render.use_compositing = settings.use_compositor

        self._ext = _render_extension(settings.file_format)
        self._frames = []
        self._intrinsics = None
        self._scene_name = scene.name
        self._index = 0
        self._rendering = False
        self._render_finished = False
        self._render_cancelled = False
        self._cancel_sent = False
        self._retry_counts = {}

        settings.render_in_progress = True
        settings.render_stop_requested = False
        settings.render_progress = 0.0
        settings.render_status = f"Preparing {len(self._cameras)} views"

        wm = context.window_manager
        wm.progress_begin(0, len(self._cameras))
        self._install_handlers()
        self._timer = wm.event_timer_add(0.15, window=context.window)
        wm.modal_handler_add(self)

        try:
            self._start_current_render(context)
        except Exception as exc:
            self._cleanup(context, cancelled=True)
            self.report({'ERROR'}, f"Could not start render: {exc}")
            return {'CANCELLED'}

        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        settings = context.scene.gs_capture

        # A watchdog or external stop may already have reset the shared state.
        # Release this modal operator instead of leaving a zombie queue behind.
        if not settings.render_in_progress:
            self._remove_handlers()
            if self._timer is not None:
                try:
                    context.window_manager.event_timer_remove(self._timer)
                except Exception:
                    pass
                self._timer = None
            return {'CANCELLED'}

        if event.type == 'ESC':
            settings.render_stop_requested = True
            self._set_status(
                context,
                "GS Capture: stop requested...",
                self._index / max(1, len(self._cameras)),
            )
            if self._rendering and not self._cancel_sent:
                try:
                    bpy.ops.render.cancel()
                    self._cancel_sent = True
                except Exception:
                    # If Blender cannot cancel from this UI context, the queue will
                    # still stop as soon as the current camera finishes.
                    self._cancel_sent = True
            return {'PASS_THROUGH'}

        if event.type != 'TIMER':
            return {'PASS_THROUGH'}

        # If the user requested Stop, cancellation is terminal. Otherwise an
        # unexpected cancellation/interruption should restart the same checked camera
        # so the batch still finishes all requested views.
        if self._render_cancelled:
            self._rendering = False
            if settings.render_stop_requested:
                try:
                    if self._frames:
                        self._write_manifest(context)
                except OSError:
                    pass
                self._cleanup(context, cancelled=True)
                self.report({'WARNING'}, "GS Capture render stopped")
                return {'CANCELLED'}
            return self._retry_interrupted_render(context, reason="render interrupted")

        # Handle completion before the stop-request branch. This fixes the old
        # deadlock where Stop was requested just as render_complete fired and the
        # modal operator kept trying to cancel an already-finished render forever.
        if self._render_finished:
            self._render_finished = False
            self._rendering = False

            try:
                self._record_current_camera(context)
            except Exception as exc:
                self._cleanup(context, cancelled=True)
                self.report({'ERROR'}, f"Could not record camera data: {exc}")
                return {'CANCELLED'}

            total = len(self._cameras)
            finished = self._index + 1
            finished_cam = self._cameras[self._index]
            self._retry_counts.pop(int(finished_cam.get("gs_index", self._index)), None)
            context.window_manager.progress_update(finished)
            self._set_status(
                context,
                f"GS Capture: finished {finished}/{total} - {self._cameras[self._index].name}",
                finished / total,
            )
            self._index += 1

            # If Stop was pressed while this camera was rendering, keep the
            # completed image, write the partial transforms file, restore all
            # scene settings, and fully release the queue state.
            if settings.render_stop_requested:
                try:
                    if self._frames:
                        self._write_manifest(context)
                except OSError as exc:
                    self.report({'WARNING'}, f"Stopped safely, but transforms.json could not be written: {exc}")
                completed = self._index
                self._cleanup(context, cancelled=True)
                context.scene.gs_capture.render_status = f"Stopped after {completed}/{total} views"
                context.scene.gs_capture.render_progress = completed / max(1, total)
                self._tag_redraw(context)
                self.report({'WARNING'}, f"GS Capture stopped after {completed}/{total} views")
                return {'CANCELLED'}

            if self._index >= total:
                try:
                    self._write_manifest(context)
                except OSError as exc:
                    self.report({'WARNING'}, f"Images rendered, but transforms.json could not be written: {exc}")
                self._cleanup(context, cancelled=False)
                self.report({'INFO'}, f"Rendered {total} GS views to {self._out_dir}")
                return {'FINISHED'}

            try:
                self._start_current_render(context)
            except Exception as exc:
                self._cleanup(context, cancelled=True)
                self.report({'ERROR'}, f"Render queue stopped: {exc}")
                return {'CANCELLED'}

            return {'PASS_THROUGH'}

        if settings.render_stop_requested:
            # Request Blender to abort the active render once. If the operator
            # cannot be cancelled from the current area, simply wait for this
            # one camera to finish, then the completion branch above ends the
            # queue without starting another camera.
            if self._rendering and not self._cancel_sent:
                self._set_status(
                    context,
                    "GS Capture: stopping current render...",
                    self._index / max(1, len(self._cameras)),
                )
                try:
                    bpy.ops.render.cancel()
                except Exception:
                    pass
                self._cancel_sent = True
                return {'PASS_THROUGH'}

            if not self._rendering:
                try:
                    if self._frames:
                        self._write_manifest(context)
                except OSError:
                    pass
                self._cleanup(context, cancelled=True)
                self.report({'WARNING'}, "GS Capture render stopped")
                return {'CANCELLED'}

        return {'PASS_THROUGH'}


class GS_OT_set_selected_collision_surface(Operator):
    bl_idname = "gs_capture.set_selected_collision_surface"
    bl_label = "Set Selected as Ground"
    bl_description = "Use the active mesh object as the ground/collision surface"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        obj = context.view_layer.objects.active
        if obj is None or obj.type != 'MESH' or obj.get("gs_capture_helper", False):
            self.report({'ERROR'}, "Select a mesh object to use as the collision surface")
            return {'CANCELLED'}
        settings = context.scene.gs_capture
        settings.collision_object = obj
        settings.collision_enabled = True
        self.report({'INFO'}, f"Collision surface set to {obj.name}")
        return {'FINISHED'}


class GS_OT_capture_framing_reference(Operator):
    bl_idname = "gs_capture.capture_framing_reference"
    bl_label = "Capture Reference Framing"
    bl_description = "Store the Reference Camera distance, lens and sensor as the 1x framing baseline"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.gs_capture
        camera = settings.reference_camera
        if camera is None or camera.type != 'CAMERA' or _is_gs_camera(camera):
            self.report({'ERROR'}, "Choose a valid Reference Camera first")
            return {'CANCELLED'}
        try:
            distance = _capture_framing_reference(context.scene, settings, camera)
        except ValueError as exc:
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}
        self.report({'INFO'}, f"Captured framing baseline at {distance:.3f} m")
        return {'FINISHED'}


class GS_OT_apply_framing_scale(Operator):
    bl_idname = "gs_capture.apply_framing_scale"
    bl_label = "Apply Scale + Regenerate"
    bl_description = "Scale the camera radius and focal length together so the target-plane framing stays consistent"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.gs_capture
        if settings.framing_reference_distance <= 1e-6:
            self.report({'ERROR'}, "Capture Reference Framing first, or Build From Reference Camera")
            return {'CANCELLED'}

        settings.radius = settings.framing_reference_distance * settings.framing_scale
        settings.lens = _matched_lens_for_radius(settings, settings.radius)
        data = bpy.data.cameras.get(CAM_DATA_NAME)
        if data is not None:
            data.lens = settings.lens
            data.sensor_width = settings.sensor_width

        if _rig_object(CONTROL_NAME) is not None:
            try:
                count = _rebuild_existing(context)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}

            # Reassert the matched optics after regeneration. The reference camera
            # remains the untouched 1x baseline; generated GS cameras get the
            # scaled focal length and radius.
            data = bpy.data.cameras.get(CAM_DATA_NAME)
            if data is not None:
                data.lens = settings.lens
                data.sensor_width = settings.sensor_width
            context.view_layer.update()
            self.report(
                {'INFO'},
                f"Applied {settings.framing_scale:.2f}x: radius {settings.radius:.3f} m, "
                f"lens {settings.lens:.2f} mm; regenerated {count} cameras"
            )
        else:
            self.report({'INFO'}, f"Scale prepared: radius {settings.radius:.3f} m, lens {settings.lens:.2f} mm")
        return {'FINISHED'}


class GS_OT_match_lens_to_radius(Operator):
    bl_idname = "gs_capture.match_lens_to_radius"
    bl_label = "Match Lens to Current Radius"
    bl_description = "Keep the current radius and calculate the focal length that preserves the captured target framing"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = context.scene.gs_capture
        if settings.framing_reference_distance <= 1e-6:
            self.report({'ERROR'}, "Capture Reference Framing first, or Build From Reference Camera")
            return {'CANCELLED'}

        settings.framing_scale = settings.radius / settings.framing_reference_distance
        settings.lens = _matched_lens_for_radius(settings, settings.radius)
        data = bpy.data.cameras.get(CAM_DATA_NAME)
        if data is not None:
            data.lens = settings.lens
            data.sensor_width = settings.sensor_width

        if _rig_object(CONTROL_NAME) is not None:
            try:
                _rebuild_existing(context)
            except ValueError as exc:
                self.report({'ERROR'}, str(exc))
                return {'CANCELLED'}
            data = bpy.data.cameras.get(CAM_DATA_NAME)
            if data is not None:
                data.lens = settings.lens
                data.sensor_width = settings.sensor_width
            context.view_layer.update()
        self.report({'INFO'}, f"Matched lens to {settings.lens:.2f} mm at {settings.radius:.3f} m")
        return {'FINISHED'}


class GS_OT_stop_render_views(Operator):
    bl_idname = "gs_capture.stop_render_views"
    bl_label = "Stop Render"
    bl_description = "Stop the active GS Capture render and fully reset the queue"

    def execute(self, context):
        scene = context.scene
        settings = scene.gs_capture
        if not settings.render_in_progress:
            self.report({'WARNING'}, "No GS Capture render is running")
            return {'CANCELLED'}

        settings.render_stop_requested = True
        settings.render_status = "GS Capture: stopping..."

        # Send the same ESC event a user would use to cancel Blender's active
        # render operator. This is more reliable than calling a non-existent
        # render.cancel operator from an arbitrary UI context.
        try:
            window = context.window
            if window is not None and hasattr(window, "event_simulate"):
                window.event_simulate(type='ESC', value='PRESS')
                window.event_simulate(type='ESC', value='RELEASE')
        except Exception:
            pass

        # Independent watchdog: if render_cancel / modal events are lost, clear
        # the queue automatically as soon as Blender no longer has a render job.
        _schedule_stop_watchdog(scene.name)

        # If no render job is active right now, the watchdog can resolve almost
        # immediately; force a redraw so the button/state updates without waiting.
        try:
            for window in context.window_manager.windows:
                for area in window.screen.areas:
                    area.tag_redraw()
        except Exception:
            pass

        self.report({'INFO'}, "Stopping GS Capture render")
        return {'FINISHED'}


class GS_OT_reset_render_state(Operator):
    bl_idname = "gs_capture.reset_render_state"
    bl_label = "Reset Render State"
    bl_description = "Force-clear a stale GS queue after the active Blender render has ended"

    def execute(self, context):
        scene = context.scene
        settings = scene.gs_capture
        if _is_render_job_running():
            self.report({'WARNING'}, "Blender is still rendering. Press Esc once, then use Reset Render State if needed.")
            return {'CANCELLED'}

        _restore_render_state_from_scene(scene, settings)
        settings.render_in_progress = False
        settings.render_stop_requested = False
        settings.render_status = "Render state reset"
        workspace = getattr(context, "workspace", None)
        if workspace is not None:
            try:
                workspace.status_text_set(text=None)
            except Exception:
                pass
        try:
            context.window_manager.progress_end()
        except Exception:
            pass
        screen = getattr(context, "screen", None)
        if screen:
            for area in screen.areas:
                try:
                    area.tag_redraw()
                except Exception:
                    pass
        self.report({'INFO'}, "GS Capture render state reset")
        return {'FINISHED'}


class GS_OT_select_all_render_cameras(Operator):
    bl_idname = "gs_capture.select_all_render_cameras"
    bl_label = "Select All Cameras"
    bl_description = "Enable all GS cameras for rendering"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not _rig_cameras():
            self.report({'WARNING'}, "No GS cameras found")
            return {'CANCELLED'}
        _set_all_render_camera_flags(True)
        self.report({'INFO'}, "Enabled all GS cameras for rendering")
        return {'FINISHED'}


class GS_OT_select_no_render_cameras(Operator):
    bl_idname = "gs_capture.select_no_render_cameras"
    bl_label = "Select No Cameras"
    bl_description = "Disable all GS cameras for rendering"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        if not _rig_cameras():
            self.report({'WARNING'}, "No GS cameras found")
            return {'CANCELLED'}
        _set_all_render_camera_flags(False)
        self.report({'INFO'}, "Disabled all GS cameras for rendering")
        return {'FINISHED'}


class GS_PT_capture_panel(Panel):
    bl_label = "GS Capture Dome"
    bl_idname = "GS_PT_capture_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "GS Capture"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        settings = scene.gs_capture

        # ---- Setup ---------------------------------------------------------
        header, body = layout.panel("GS_CAPTURE_SETUP", default_closed=False)
        header.label(text="Setup", icon='OBJECT_ORIGIN')
        if body:
            box = body.column(align=True)
            box.prop(settings, "capture_approach")

            if settings.capture_approach == 'ORBIT':
                box.separator()
                box.label(text="Target")
                box.prop(settings, "target_mode")
                if settings.target_mode != 'CURSOR':
                    box.prop(settings, "target_object")
                    box.operator("gs_capture.set_selected_target", icon='EYEDROPPER')
                else:
                    box.label(text="Uses current 3D Cursor position", icon='PIVOT_CURSOR')

            box.separator()
            box.label(text="Reference Camera")
            box.prop(settings, "reference_camera")
            row = box.row(align=True)
            row.operator("gs_capture.set_selected_reference_camera", icon='EYEDROPPER')
            refresh = row.row(align=True)
            refresh.enabled = settings.reference_camera is not None
            refresh.operator("gs_capture.refresh_reference_camera_settings", icon='FILE_REFRESH', text="Refresh Optics")
            box.prop(settings, "copy_reference_camera_settings")
            if settings.reference_camera:
                ref = settings.reference_camera.data
                if settings.capture_approach == 'LOOK_AROUND':
                    box.label(text=f"Fixed center: {settings.reference_camera.name}", icon='CAMERA_DATA')
                    box.label(text="GS_CAM_000 exactly matches this camera orientation.", icon='INFO')
                else:
                    box.label(text=f"Pole view: {settings.reference_camera.name}", icon='CAMERA_DATA')
                box.label(text=f"Ref optics: {ref.lens:.2f}mm / sensor {ref.sensor_width:.2f}mm", icon='INFO')

        # ---- Capture Geometry ----------------------------------------------
        header, body = layout.panel("GS_CAPTURE_DOME", default_closed=False)
        header.label(text="Look Around" if settings.capture_approach == 'LOOK_AROUND' else "Dome", icon='OUTLINER_OB_CAMERA')
        if body:
            box = body.column(align=True)

            if settings.capture_approach == 'LOOK_AROUND':
                box.prop(settings, "lookaround_camera_count")
                box.label(text="All views share one position; only orientation changes.", icon='INFO')
                box.label(text="Directions use an even full-sphere Fibonacci distribution.")
                if settings.reference_camera:
                    try:
                        hfov = math.degrees(settings.reference_camera.data.angle_x)
                        vfov = math.degrees(settings.reference_camera.data.angle_y)
                        approx_spacing = math.degrees(math.sqrt(4.0 * math.pi / max(1, settings.lookaround_camera_count)))
                        box.label(text=f"Ref FOV: {hfov:.1f}° x {vfov:.1f}° | avg spacing ~{approx_spacing:.1f}°")
                    except Exception:
                        pass
                box.prop(settings, "show_dome_guide", text="Show Direction Guide")
            else:
                box.prop(settings, "distribution")
                if settings.distribution == 'RINGS':
                    box.prop(settings, "ring_count")
                    box.prop(settings, "ring_density")
                    layout_data = _adaptive_ring_layout(settings.cap_angle, settings.ring_count, settings.ring_density)
                    counts = [count for count, _theta, _phase in layout_data]
                    total_requested = 1 + sum(counts)
                    short = " + ".join(str(v) for v in ([1] + counts[:6]))
                    if len(counts) > 6:
                        short += " + ..."
                    box.label(text=f"Estimated: {total_requested} cameras ({short})", icon='INFO')
                elif settings.distribution == 'FIBONACCI':
                    box.prop(settings, "camera_count")
                    box.label(text=f"Exact request: {settings.camera_count} cameras", icon='INFO')
                else:
                    box.label(text="Legacy 26 views: 1 + 5 + 8 + 12")
                box.prop(settings, "radius")
                box.prop(settings, "cap_angle")
                box.prop(settings, "show_dome_guide")

            box.separator()
            box.label(text="Camera Optics")
            box.prop(settings, "lens")
            box.prop(settings, "sensor_width")
            if settings.reference_camera:
                box.label(text=f"Synced from ref cam: {settings.reference_camera.name}", icon='LINKED')

            box.separator()
            if settings.capture_approach == 'LOOK_AROUND':
                build = box.row()
                build.enabled = settings.reference_camera is not None
                build.operator("gs_capture.build_from_active_camera", icon='CAMERA_DATA', text="Build Look-Around Rig")
            else:
                box.operator("gs_capture.build_rig", icon='OUTLINER_OB_CAMERA', text="Build Dome")
                box.operator("gs_capture.build_from_active_camera", icon='CAMERA_DATA', text="Build From Reference Camera")

            regen = box.row()
            regen.enabled = _rig_object(CONTROL_NAME) is not None
            regen.operator("gs_capture.rebuild_rig", icon='FILE_REFRESH', text="Regenerate Rig" if settings.capture_approach == 'LOOK_AROUND' else "Regenerate Dome")

            cams = _rig_cameras()
            if cams:
                label = f"Current rig: {len(cams)} cameras"
                if settings.capture_approach == 'ORBIT' and settings.last_collision_skipped:
                    label += f"  |  skipped {settings.last_collision_skipped}"
                box.label(text=label, icon='CHECKMARK')

        # ---- Collision / Ground ------------------------------------------
        header, body = layout.panel("GS_CAPTURE_COLLISION", default_closed=True)
        header.label(text="Collision / Ground", icon='MESH_GRID')
        if body:
            box = body.column(align=True)
            if settings.capture_approach == 'LOOK_AROUND':
                box.label(text="Not used in Fixed Position / Look Around mode.", icon='INFO')
                box.label(text="All generated cameras stay at the Reference Camera position.")
            else:
                box.prop(settings, "collision_enabled")
                controls = box.column(align=True)
                controls.enabled = settings.collision_enabled
                controls.prop(settings, "collision_object")
                controls.operator("gs_capture.set_selected_collision_surface", icon='EYEDROPPER', text="Set Selected as Ground")
                controls.prop(settings, "collision_clearance")
                controls.label(text="Rule: camera must stay above surface + clearance.", icon='INFO')
                controls.label(text="Uses evaluated terrain/GN mesh and world-Z ray tests.")
                if settings.last_collision_skipped:
                    controls.label(text=f"Last build skipped: {settings.last_collision_skipped} cameras", icon='CHECKMARK')

        # ---- Framing / Scale ---------------------------------------------
        header, body = layout.panel("GS_CAPTURE_FRAMING", default_closed=True)
        header.label(text="Framing / Scale", icon='CAMERA_DATA')
        if body:
            box = body.column(align=True)
            if settings.capture_approach == 'LOOK_AROUND':
                box.label(text="Not applicable to Fixed Position / Look Around.", icon='INFO')
                box.label(text="The camera center never moves, so reference perspective stays unchanged.")
            else:
                box.operator("gs_capture.capture_framing_reference", icon='CAMERA_DATA', text="Capture Reference Framing")
                if settings.framing_reference_distance > 1e-6:
                    ref = box.box()
                    ref.label(text=f"1x distance: {settings.framing_reference_distance:.3f} m")
                    ref.label(text=f"1x lens: {settings.framing_reference_lens:.2f} mm")
                    ref.label(text=f"1x sensor: {settings.framing_reference_sensor:.2f} mm")

                    box.prop(settings, "framing_scale")
                    target_radius = settings.framing_reference_distance * settings.framing_scale
                    predicted_lens = _matched_lens_for_radius(settings, target_radius)
                    preview = box.box()
                    preview.label(text=f"Target radius: {target_radius:.3f} m")
                    preview.label(text=f"Matched lens: {predicted_lens:.2f} mm")
                    preview.label(text="Keeps target-plane framing; optical FOV changes.", icon='INFO')
                    box.operator("gs_capture.apply_framing_scale", icon='FILE_REFRESH', text="Apply Scale + Regenerate")
                    box.separator()
                    current = box.box()
                    current.label(text=f"Applied radius: {settings.radius:.3f} m")
                    current.label(text=f"Applied GS lens: {settings.lens:.2f} mm")
                    current.label(text="Reference camera stays unchanged as the 1x baseline.", icon='INFO')
                    box.operator("gs_capture.match_lens_to_radius", icon='DRIVER_DISTANCE', text="Match Lens to Current Radius")
                else:
                    box.label(text="Build From Reference Camera or capture a framing baseline first.", icon='INFO')

        # ---- Orbit / Framing ----------------------------------------------
        header, body = layout.panel("GS_CAPTURE_ORBIT", default_closed=True)
        header.label(text="Orbit / Framing", icon='ORIENTATION_GIMBAL')
        if body:
            box = body.column(align=True)
            control = _rig_object(CONTROL_NAME)
            if control:
                box.prop(control, "rotation_euler", text="Rig Rotation")
                row = box.row(align=True)
                row.operator("gs_capture.select_control", icon='EMPTY_AXIS')
                row.operator("gs_capture.select_target", icon='PIVOT_CURSOR')
                box.label(text="Rotate GS_RIG_CONTROL to rotate all viewing directions." if settings.capture_approach == 'LOOK_AROUND' else "Rotate GS_RIG_CONTROL to orbit the whole dome.")
                box.label(text="Camera position stays fixed at the Reference Camera." if settings.capture_approach == 'LOOK_AROUND' else "All cameras remain aimed at the target.")
                box.operator("gs_capture.delete_rig", icon='TRASH')
            else:
                box.label(text="Build a dome first.", icon='INFO')

        # ---- Render / Export ----------------------------------------------
        header, body = layout.panel("GS_CAPTURE_RENDER", default_closed=False)
        header.label(text="Render / Export", icon='RENDER_STILL')
        if body:
            box = body.column(align=True)
            box.prop(settings, "output_dir")
            box.prop(settings, "file_format")

            box.separator()
            box.label(text="Capture Resolution")
            box.prop(settings, "resolution_preset")
            custom_row = box.row()
            custom_row.enabled = settings.resolution_preset == 'CUSTOM'
            custom_row.prop(settings, "render_percentage", slider=True)

            out_w, out_h, equivalent_pct = _capture_output_dimensions(scene, settings)
            base_w = int(scene.render.resolution_x)
            base_h = int(scene.render.resolution_y)
            max_w = base_w * 2
            max_h = base_h * 2
            preview = box.box()
            preview.label(text=f"Output: {out_w} x {out_h}", icon='IMAGE_DATA')
            preview.label(text=f"Equivalent scale: {equivalent_pct:.1f}%")
            preview.label(text=f"Base: {base_w} x {base_h}   Max: {max_w} x {max_h}")
            if settings.resolution_preset not in {'CUSTOM', 'ORIGINAL', 'DOUBLE'}:
                preview.label(text="Preset height; current scene aspect ratio is preserved.")

            box.separator()
            box.prop(settings, "use_compositor")
            box.prop(settings, "export_transforms")

            box.separator()
            box.label(text="Camera Selection")
            box.prop(settings, "render_camera_mode")
            if settings.render_camera_mode == 'CUSTOM':
                sel_box = box.box()
                row = sel_box.row(align=True)
                row.operator("gs_capture.select_all_render_cameras", icon='CHECKBOX_HLT', text="Select All")
                row.operator("gs_capture.select_no_render_cameras", icon='CHECKBOX_DEHLT', text="Select None")
                cams = _rig_cameras()
                if cams:
                    enabled_count = sum(1 for cam in cams if getattr(cam, 'gs_render_enabled', True))
                    sel_box.label(text=f"Enabled: {enabled_count}/{len(cams)} cameras")
                    col = sel_box.column(align=True)
                    for cam in cams:
                        col.prop(cam, "gs_render_enabled", text=cam.name)
                else:
                    sel_box.label(text="Build a dome first.", icon='INFO')

            engine_names = {
                'CYCLES': 'Cycles',
                'BLENDER_EEVEE_NEXT': 'EEVEE',
                'BLENDER_WORKBENCH': 'Workbench',
            }
            engine = engine_names.get(scene.render.engine, scene.render.engine)
            box.label(text=f"Uses scene engine: {engine}", icon='SCENE_DATA')
            box.label(text=f"Scene base: {scene.render.resolution_x} x {scene.render.resolution_y} @ 100%")

            box.separator()
            if settings.render_in_progress:
                progress_box = box.box()
                try:
                    progress_box.progress(
                        factor=settings.render_progress,
                        type='BAR',
                        text=settings.render_status or "Rendering GS views",
                    )
                except Exception:
                    progress_box.label(text=settings.render_status or "Rendering GS views", icon='RENDER_STILL')
                progress_box.label(text=f"{settings.render_progress * 100.0:.0f}% complete")
            render_row = box.row(align=True)
            start = render_row.row(align=True)
            start.enabled = not settings.render_in_progress
            start.operator("gs_capture.render_views", icon='RENDER_STILL')
            stop = render_row.row(align=True)
            stop.enabled = settings.render_in_progress
            stop.operator("gs_capture.stop_render_views", icon='CANCEL', text="Stop Render")
            if settings.render_in_progress:
                reset_row = box.row()
                reset_row.enabled = not _is_render_job_running()
                reset_row.operator("gs_capture.reset_render_state", icon='FILE_REFRESH', text="Reset Render State")
            box.label(text="Current timeline frame, scene engine and render settings are preserved.")
            box.label(text="Stop resets the queue; completed images remain on disk.", icon='INFO')


classes = (
    GSCaptureSettings,
    GS_OT_build_rig,
    GS_OT_build_from_active_camera,
    GS_OT_rebuild_rig,
    GS_OT_delete_rig,
    GS_OT_select_control,
    GS_OT_select_target,
    GS_OT_set_selected_target,
    GS_OT_set_selected_reference_camera,
    GS_OT_refresh_reference_camera_settings,
    GS_OT_set_selected_collision_surface,
    GS_OT_capture_framing_reference,
    GS_OT_apply_framing_scale,
    GS_OT_match_lens_to_radius,
    GS_OT_render_views,
    GS_OT_stop_render_views,
    GS_OT_reset_render_state,
    GS_OT_select_all_render_cameras,
    GS_OT_select_no_render_cameras,
    GS_PT_capture_panel,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Object.gs_render_enabled = BoolProperty(
        name="Render In GS Batch",
        description="If enabled, this GS dome camera will be included when Render Cameras is set to Selected Cameras",
        default=True,
    )
    bpy.types.Scene.gs_capture = PointerProperty(type=GSCaptureSettings)
    for scene in bpy.data.scenes:
        try:
            scene.gs_capture.render_in_progress = False
            scene.gs_capture.render_stop_requested = False
            scene.gs_capture.restore_state_valid = False
            scene.gs_capture.render_progress = 0.0
            scene.gs_capture.render_status = ""
        except Exception:
            pass


def unregister():
    if hasattr(bpy.types.Scene, "gs_capture"):
        del bpy.types.Scene.gs_capture
    if hasattr(bpy.types.Object, "gs_render_enabled"):
        del bpy.types.Object.gs_render_enabled
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
