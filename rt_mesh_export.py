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
from bpy.props import BoolProperty
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


    @classmethod
    def poll(cls, context):
        return context.active_object is not None


    def execute(self, context):
        me = context.active_object
        bm = bmesh.new()
        bm.from_mesh(me.data)
        ex = get_ex(bm, me)
        bm.free()

        indent = None
        if self.enable_indent:
            indent = 4

        write_file(self.filepath, json.dumps(ex, indent=indent))
        return {"FINISHED"}


def menu_export(self, context):
    self.layout.operator(Export.bl_idname, text="Export RT JSON")


def register():
    bpy.utils.register_class(Export)
    bpy.types.TOPBAR_MT_file_export.append(menu_export)


def unregister():
    bpy.utils.unregister_class(Export)
    bpy.types.TOPBAR_MT_file_export.remove(menu_export)


def get_ex(bm, me):
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


def write_file(path, text):
    f = open(path, "w")
    f.write(text)
    f.close()
