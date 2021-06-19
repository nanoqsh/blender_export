bl_info = {
    "name": "RT Mesh Export",
    "description": "Exports meshes in RT JSON file.",
    "author": "nanoqsh",
    "version": (1, 0),
    "blender": (2, 80, 0),
    "support": "COMMUNITY",
    "category": "Import-Export",
}


import bpy
from bpy_extras.io_utils import ExportHelper
from bpy.props import BoolProperty, EnumProperty
from bpy.types import Operator
from dataclasses import dataclass
from collections import defaultdict
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
        if context.mode != 'OBJECT':
            return False

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

        if self.to_export == "MESH":
            ex = self.export_mesh(context)
        elif self.to_export == "ACTION":
            ex = self.export_action(context)
        elif self.to_export == "SKELETON":
            ex = self.export_skeleton(context)
        else:
            ex = None

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
    old_indxs = []
    groups = defaultdict(list)
    slots = defaultdict(list)

    triangulate(bm)

    fm = bm.faces.layers.face_map.verify()
    uv_layer = me.data.uv_layers.active.data
    for face_idx, face in enumerate(bm.faces):
        assert len(face.verts) == 3

        map_idx = face[fm]
        if map_idx >= 0:
            slot_name = me.face_maps[map_idx].name
            slots[slot_name].append(face_idx)

        for vert, loop in zip(face.verts, face.loops):
            x = vert.co.x
            y = vert.co.y
            z = vert.co.z
            
            q = vert.normal.x
            w = vert.normal.y
            e = vert.normal.z

            if loop.index < 0:
                raise ValueError(f"Failed to get uv map. Try to triangulate mesh")

            uv = uv_layer[loop.index].uv
            u = uv.x
            v = uv.y
            
            verts.append({
                "c": norm_list([x, z, -y]),
                "n": norm_list([q, e, -w]),
                "t": [u, 1.0 - v],
                "map_idx": map_idx,
            })
            old_indxs.append(vert.index)

    verts, indxs = make_indexes(verts)
    for v in verts:
        del v["map_idx"]

    ex = {
        "verts": verts,
        "indxs": indxs,
    }

    group_names = {g.index: g.name for g in me.vertex_groups}
    for v in me.data.vertices:
        for g in v.groups:
            name = group_names[g.group]
            weight = norm(g.weight)
            if weight <= 0.0:
                continue

            new_idxs = set()
            for idx, old_idx in enumerate(old_indxs):
                if old_idx == v.index:
                    new_idxs.add(indxs[idx])
            
            for new_idx in new_idxs:
                groups[name].append([new_idx, weight])

    if groups:
        ex["groups"] = groups
    
    if slots:
        ex["slots"] = slots

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
    motions = defaultdict(list)

    for c in act.fcurves:
        idx = c.array_index
        path = parse_path(c.data_path)
        if path is None:
            continue

        nodes = motions[path]
        for k in c.keyframe_points:
            frame = norm(k.co[0])
            value = k.co[1]
            start = frame if start is None else min(start, frame)
            end = frame if end is None else max(end, frame)
            ease, curve = make_interpolation(k)
            node = {
                "f": frame,
                "v": value,
            }

            if ease is not None:
                node["e"] = ease
            
            if curve is not None:
                node["c"] = curve
            
            handles = Handles(k.handle_left, k.handle_right)
            nodes.append((idx, node, handles))
    
    objects = defaultdict(list)
    for path, nodes in motions.items():
        name, kind = path
        obj = objects[name]
        motion = []
        for idx, node, handles in nodes:
            mnode = None
            for mot in motion:
                if abs(mot["f"] - node["f"]) < ACTION_PRECISION:
                    mnode = mot
                    break
            else:
                values = [None, None, None]
                if kind == "rot":
                    values.append(None)
                mnode = {
                    "f": node["f"],
                    "d": None,
                    "k": kind,
                    "v": values,
                }
                motion.append(mnode)

            value = {
                "r": [node["v"], None],
                "handles": handles,
            }

            for key in ["e", "c"]:
                if key in node:
                    value[key] = node[key]

            mnode["v"][idx] = value
        
        motion.sort(key=lambda mot: mot["f"])
        for i in range(len(motion) - 1):
            curr = motion[i]
            next = motion[i + 1]
            duration = next["f"] - curr["f"]
            if duration < ACTION_PRECISION:
                raise ValueError("Duration is too short")
            
            curr["d"] = duration
            for c, n in zip(curr["v"], next["v"]):
                next_val = n["r"][0]
                delta = next_val - c["r"][0]
                c["r"][1] = n["r"][0]
                if "c" not in c or c["c"] == "bezier":
                    cx, cy = c["handles"].right
                    nx, ny = n["handles"].left
                    c["b"] = [cx, cy, nx, ny]
        
        motion = motion[:-1]
        for node in motion:
            for v in node["v"]:
                del v["handles"]
                fr, to = v["r"]
                if "b" in v and abs(to - fr) < ACTION_PRECISION:
                    v["c"] = "line"
                    del v["b"]

            # Swap rot values from [w, x, y, z] to [x, y, z, w]
            if node["k"] == "rot":
                w, x, y, z = node["v"]
                node["v"] = [x, y, z, w]

        obj.extend(motion)
    
    if start is None or end is None:
        raise ValueError("Failed to export an empty action")
    
    return {
        "range": [start, end],
        "objects": objects,
    }


def make_interpolation(keyframe):
    curve = keyframe.interpolation

    if keyframe.easing == "AUTO":
        ease = "out" if curve in ["BACK", "BOUNCE", "ELASTIC"] else "in"
    elif keyframe.easing == "EASE_IN":
        ease = "in"
    elif keyframe.easing == "EASE_OUT":
        ease = "out"
    elif keyframe.easing == "EASE_IN_OUT":
        ease = "in_out"
    else:
        ease = None
    
    # Pass ease if value is default or interpolation doesn't use easing
    if ease == "in" or curve in ["CONSTANT", "LINEAR", "BEZIER"]:
        ease = None
    
    # Pass curve if value is default
    if curve == "BEZIER":
        curve = None
    elif curve == "CONSTANT":
        curve = "const"
    elif curve == "LINEAR":
        curve = "line"
    else:
        curve = curve.lower()
    
    return ease, curve


def parse_path(path):
    nl = path.find("[\"")
    nr = path.rfind("\"].")
    name = path[nl + 2:nr]

    if path.endswith("rotation_euler"):
        raise ValueError("A quaternion rotation was expected, not Euler angles")
    elif path.endswith("rotation_quaternion"):
        kind = "rot"
    elif path.endswith("location"):
        kind = "pos"
    elif path.endswith("scale"):
        kind = "scl"
    else:
        return None

    return name, kind


def export_skeleton(skeleton):
    bones = {}

    # Find the root
    for b in skeleton.bones:
        if b.parent is None:
            root = b.name
            break
    else:
        root = None

    for b in skeleton.bones:
        head = b.head_local
        tail = b.tail_local
        mat = b.matrix_local
        rot = mat.to_quaternion()
        children = [c.name for c in b.children]
        bone = {
            "h": norm_list([head.x, head.z, -head.y]),
            "t": norm_list([tail.x, tail.z, -tail.y]),
            "r": rot_adjust(rot),
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
    if q == -0.0:
        q = 0.0
    
    return q


def norm_list(vs):
    return [norm(v) for v in vs]


def rot_adjust(rot):
    res = ROT_ADJUSTMENT @ rot
    return [res.x, res.y, res.z, res.w]


@dataclass
class Handles:
    left: list[float]
    right: list[float]


ACTION_PRECISION = 0.000_001
ROT_ADJUSTMENT = mathutils.Quaternion((0.70710678118, -0.70710678118, 0, 0))
