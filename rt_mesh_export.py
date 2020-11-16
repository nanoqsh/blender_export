bl_info = {
    "name": "RT Mesh Export",
    "description": "Exports meshes in RT JSON file.",
    "author": "nanolsn",
    "version": (1, 0),
    "blender": (2, 80, 0),
    "support": "COMMUNITY",
    "category": "Import-Export",
}


import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
import bmesh
import json


class Export(Operator, ExportHelper):
    """Export RT JSON"""
    bl_idname = "export.rt_json"
    bl_label = "Export RT JSON"
    bl_options = {"REGISTER"}

    filename_ext = ".json"

    enable_indent: BoolProperty(
        name="Enable Indent",
        description="Exports JSON with indents",
        default=False,
    )

    to_export: EnumProperty(
        name="To Export",
        description="Selects what type of object to export",
        default="MESH",
        items=[
            ("MESH", "Mesh", "Export a Mesh"),
            ("ACTION", "Action", "Export an Action"),
            ("SKELETON", "Skeleton", "Export a Skeleton"),
        ],
    )


    def check_selection(self, context):
        obj = context.active_object
        ok = True
        if self.to_export == "MESH":
            ok = (
                obj is not None
                and obj.data is not None
                and isinstance(obj.data, bpy.types.Mesh)
                )
        elif self.to_export == "ACTION":
            ok = (obj is not None
                and obj.animation_data is not None
                and obj.animation_data.action is not None
                and isinstance(obj.animation_data.action, bpy.types.Action)
                )
        elif self.to_export == "SKELETON":
            ok = (obj is not None
                and obj.data is not None
                and isinstance(obj.data, bpy.types.Armature)
                )
        return ok


    def execute(self, context):
        if not self.check_selection(context):
            self.report({"WARNING"}, "Object is not selected")
            return {"CANCELLED"}

        ex = None
        if self.to_export == "MESH":
            ex = self.export_mesh(context)
        elif self.to_export == "ACTION":
            ex = self.export_action(context)
        elif self.to_export == "SKELETON":
            ex = self.export_skeleton(context)

        indent = None
        if self.enable_indent:
            indent = 4

        write_file(self.filepath, json.dumps(ex, indent=indent))
        self.report({"INFO"}, f"File {self.filepath} saved")
        return {"FINISHED"}
    

    def export_mesh(self, context):
        me = context.active_object
        bm = bmesh.new()
        bm.from_mesh(me.data)
        ex = export_mesh(bm, me)
        bm.free()
        return ex


    def export_action(self, context):
        act = context.active_object.animation_data.action
        ex = export_action(act)
        return ex
    

    def export_skeleton(self, context):
        skeleton = context.active_object.data
        ex = export_skeleton(skeleton)
        return ex


def menu_export(self, context):
    self.layout.operator(Export.bl_idname, text="Export RT JSON")


def register():
    bpy.utils.register_class(Export)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.utils.unregister_class(Export)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)


def write_file(path, text):
    f = open(path, "w")
    f.write(text)
    f.close()


def export_mesh(bm, me):
    verts = []
    indxs = []
    groups = {}

    triangulate(bm)

    uv_layer = bm.loops.layers.uv.active
    for f in bm.faces:
        for vert in f.verts:
            x = vert.co.x
            y = vert.co.y
            z = vert.co.z
            
            q = vert.normal.x
            w = vert.normal.y
            e = vert.normal.z

            uv = None
            for l in vert.link_loops:
                uv_data = l[uv_layer]
                uv = uv_data.uv
                break

            u = uv[0]
            v = uv[1]

            verts.append({
                "c": [x, y, z],
                "n": [q, w, e],
                "t": [u, v],
            })
            indxs.append(vert.index)

    for g in me.vertex_groups:
        groups[g.name] = []

    group_names = {g.index: g.name for g in me.vertex_groups}
    for v in me.data.vertices:
        for g in v.groups:
            name = group_names[g.group]
            groups[name].append({
                "i": v.index,
                "w": g.weight,
            })

    ex = {
        "verts": verts,
        "indxs": indxs,
    }

    if groups:
        ex["groups"] = groups

    return ex


def triangulate(bm):
    need_triangulate = False
    for f in bm.faces:
        flen = len(f.verts)
        if flen > 3:
            need_triangulate = True
        elif flen < 3:
            raise ValueError(f"Failed to export mesh with face len = {flen}")

    if need_triangulate:
        bmesh.ops.triangulate(bm, faces=bm.faces[:])


ANIMATION_PRECISION = 0.000_000_01


def export_action(act):
    frames = []
    bones = {}

    for c in act.fcurves:
        axis = index_to_axis(c.array_index)
        p = parse_path(c.data_path)
        if p is None:
            continue
        name, ty = p

        if name not in bones:
            bones[name] = {}

        bone = bones[name]
        for k in c.keyframe_points:
            frame = k.co[0]
            frame_idx = None

            # Find the frame index
            for i, f in enumerate(frames):
                if abs(f - frame) <= ANIMATION_PRECISION:
                    frame_idx = i
                    break
            # Add new frame if not found
            else:
                frame_idx = len(frames)
                frames.append(frame)

            value = k.co[1]
            intr = k.interpolation
            ease = easing[k.easing]
            rec = {
                "a": axis,
                "f": frame_idx,
                "v": value,
            }

            # Include only if value is not default
            if ease != "in":
                rec["ease"] = ease

            # Include only if value is not default
            if intr != "BEZIER":
                rec["intr"] = intr
            
            # If intr type is bezier, add 'l' and 'r' attributes
            if intr == "BEZIER":
                l = k.handle_left
                r = k.handle_right
                rec["l"] = [l[0], l[1]]
                rec["r"] = [r[0], r[1]]
            
            if ty in bone:
                bone[ty].append(rec)
            else:
                bone[ty] = [rec]
    
    # Remove unnecessary frame points
    for b in bones.values():
        for rs in b.values():
            # Count the amplitude of axis
            ampls = {}

            for r in rs:
                axis = r["a"]
                val = r["v"]
                if axis in ampls:
                    low, high = ampls[axis]
                    ampls[axis] = (min(low, val), max(high, val))
                else:
                    ampls[axis] = (val, val)

            # If the amplitude is zero, then remove the entire axis
            for axis, am in ampls.items():
                low, high = am
                # But, if the constant value is not zero,
                # then leave it for first frame
                leave_first = low != 0.0
                if abs(low - high) <= ANIMATION_PRECISION:
                    p = lambda r: r["a"] != axis or (leave_first and r["f"] == 0)
                    rs = list(filter(p, rs))

    return {
        "frames": frames,
        "bones": bones,
    }


def index_to_axis(idx):
    axis = ""
    if idx == 0:
        axis = "x"
    elif idx == 1:
        axis = "y"
    elif idx == 2:
        axis = "z"
    elif idx == 3:
        axis = "w"
    return axis


def parse_path(path):
    nl = path.find("[\"")
    nr = path.rfind("\"].")
    name = path[nl + 2:nr]

    ty = None
    if path.endswith("rotation_quaternion"):
        ty = "rot"
    elif path.endswith("location"):
        ty = "pos"

    if ty is None:
        return None
    else:
        return (name, ty)


easing = {
    "AUTO": "in",
    "EASE_IN": "in",
    "EASE_OUT": "out",
    "EASE_IN_OUT": "in_out",
}


def export_skeleton(skeleton):
    bones = {}

    # Find the root
    root = None
    for b in skeleton.bones:
        if b.parent is None:
            root = b.name
            break

    for b in skeleton.bones:
        head = b.head
        tail = b.tail
        children = list(map(lambda c: c.name, b.children))
        bone = {
            "h": [head.x, head.y, head.z],
            "t": [tail.x, tail.y, tail.z],
        }

        if children:
            bone["c"] = children

        bones[b.name] = bone

    return {
        "root": root,
        "bones": bones,
    }
