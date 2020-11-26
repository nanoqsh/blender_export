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
import math
import mathutils


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
    self.layout.operator(Export.bl_idname, text="RT Format (.json)")


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
    groups = {}

    triangulate(bm)

    uv_layer = me.data.uv_layers.active.data
    for face in bm.faces:
        assert len(face.verts) == 3
        for vert, loop in zip(face.verts, face.loops):
            x = vert.co.x
            y = vert.co.y
            z = vert.co.z
            
            q = vert.normal.x
            w = vert.normal.y
            e = vert.normal.z

            uv = uv_layer[loop.index].uv
            u = uv.x
            v = uv.y
            
            verts.append({
                # swap y and z
                "c": norm_list([x, z, -y]),
                "n": norm_list([q, e, -w]),
                "t": [u, 1.0 - v], # leave uv's unnormalized
            })

    for g in me.vertex_groups:
        groups[g.name] = []

    group_names = {g.index: g.name for g in me.vertex_groups}
    for v in me.data.vertices:
        for g in v.groups:
            name = group_names[g.group]
            groups[name].append({
                "i": v.index,
                "w": norm(g.weight),
            })

    verts, indxs = make_indexes(verts)
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


def make_indexes(vs):
    indxs = []
    verts = []

    for v in vs:
        idx = None
        for i, w in enumerate(verts):
            if v == w:
                idx = i
                break
        else:
            idx = len(verts)
            verts.append(v)
        indxs.append(idx)

    return verts, indxs


def export_action(act):
    start = None
    end = None
    objects = {}

    for c in act.fcurves:
        p = parse_path(c.data_path, c.array_index)
        if p is None:
            continue
        name, ty, cord = p
        axis = f"{ty}_{cord}" 

        if name not in objects:
            objects[name] = {}

        obj = objects[name]
        for k in c.keyframe_points:
            frame = int(k.co[0])

            start = frame if start is None else min(start, frame)
            end = frame if end is None else max(end, frame)

            ease = None
            if k.easing == "AUTO":
                ease = "out" if k.interpolation in ["BACK", "BOUNCE", "ELASTIC"] else "in"
            elif k.easing == "EASE_IN":
                ease = "in"
            elif k.easing == "EASE_OUT":
                ease = "out"
            elif k.easing == "EASE_IN_OUT":
                ease = "in_out"

            value = k.co[1]
            intr = k.interpolation
            node = {
                "f": frame,
                "v": norm(value),
            }

            # Include only if value is not default
            # and interpolation uses easing
            if ease != "in" and intr not in ["CONSTANT", "LINEAR", "BEZIER"]:
                node["ease"] = ease

            # Include only if value is not default
            if intr != "BEZIER":
                named = None
                if intr == "CONSTANT":
                    named = "const"
                elif intr == "LINEAR":
                    named = "line"
                else:
                    named = intr.lower()

                node["intr"] = named
            
            # If intr type is bezier, add 'l' and 'r' attributes
            if intr == "BEZIER":
                l = k.handle_left
                r = k.handle_right
                node["l"] = norm_list([l[0], l[1]])
                node["r"] = norm_list([r[0], r[1]])
            
            if axis in obj:
                obj[axis].append(node)
            else:
                obj[axis] = [node]

    # Remove unnecessary nodes
    new_objects = {}
    for name, obj in objects.items():
        new_obj = {}
        for axis, nodes in obj.items():
            elastic = False
            flat = True
            ampl = None
            for node in nodes:
                if "intr" in node and node["intr"] == "elastic":
                    elastic = True
                
                if ("l" in node and node["l"][1] != node["v"]
                and "r" in node and node["r"][1] != node["v"]):
                    flat = False

                val = node["v"]
                if ampl is None:
                    ampl = (val, val)
                else:
                    low, high = ampl
                    ampl = (min(low, val), max(high, val))

            low, high = ampl
            if abs(low - high) < ANIMATION_PRECISION and not elastic and flat:
                if low != AXIS_DEFAULTS[axis]:
                    first_node = None
                    for node in nodes:
                        if first_node is None or node["f"] < first_node["f"]:
                            first_node = node  
                    new_obj[axis] = [first_node]
            else:
                new_obj[axis] = nodes

        if new_obj:
            new_objects[name] = new_obj

    if start is None or end is None:
        raise ValueError("Failed to export an empty action")
    
    return {
        "range": [start, end],
        "objects": new_objects,
    }


def parse_path(path, idx):
    nl = path.find("[\"")
    nr = path.rfind("\"].")
    name = path[nl + 2:nr]

    ty = None
    cord = None
    if path.endswith("rotation_quaternion"):
        ty = "rot"
        if idx == 0:
            cord = "w"
        elif idx == 1:
            cord = "x"
        elif idx == 2:
            cord = "y"
        elif idx == 3:
            cord = "z"
    elif path.endswith("location"):
        ty = "pos"
        if idx == 0:
            cord = "x"
        elif idx == 1:
            cord = "y"
        elif idx == 2:
            cord = "z"

    if ty is None:
        return None
    else:
        return (name, ty, cord)


def export_skeleton(skeleton):
    bones = {}

    # Find the root
    root = None
    for b in skeleton.bones:
        if b.parent is None:
            root = b.name
            break

    for b in skeleton.bones:
        head = b.head_local
        tail = b.tail_local
        mat = b.matrix_local
        rot = mat.to_quaternion()
        children = list(map(lambda c: c.name, b.children))
        bone = {
            # swap y and z
            "h": norm_list([head.x, head.z, -head.y]),
            "t": norm_list([tail.x, tail.z, -tail.y]),
            "r": norm_list(rot_adjust(rot)),
        }

        if children:
            bone["c"] = children

        bones[b.name] = bone

    return {
        "root": root,
        "bones": bones,
    }


def norm(v):
    q = round(v, 6)
    if q == 0.0:
        q = 0.0
    return q


def norm_list(vs):
    return list(map(norm, vs))


def rot_adjust(rot):
    res = ROT_ADJUSTMENT @ rot
    return [-res.w, res.x, res.y, res.z]


ANIMATION_PRECISION = 0.000_000_1
AXIS_DEFAULTS = {
    "pos_x": 0.0,
    "pos_y": 0.0,
    "pos_z": 0.0,
    "rot_w": 1.0,
    "rot_x": 0.0,
    "rot_y": 0.0,
    "rot_z": 0.0,
} 
ROT_ADJUSTMENT = mathutils.Quaternion((0.70710678118, -0.70710678118, 0, 0))
