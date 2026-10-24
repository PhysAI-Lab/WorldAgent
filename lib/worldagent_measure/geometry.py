"""Evaluated world-space geometry shared by the measurement helpers (requires bpy).

Measure the evaluated mesh (modifiers, simulation caches, transforms applied),
not the original data block, so solver output is what gets checked
[non-penetration-contract.en.md 'evaluated world-space BVH'].
"""

from __future__ import annotations

from dataclasses import dataclass

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree


@dataclass
class WorldMesh:
    name: str
    vertices: list  # list[Vector], world space
    triangles: list  # list[tuple[int, int, int]]

    @property
    def bbox(self) -> tuple[Vector, Vector]:
        if not self.vertices:
            raise ValueError(f"{self.name} has no vertices")
        lo = Vector((min(v.x for v in self.vertices), min(v.y for v in self.vertices), min(v.z for v in self.vertices)))
        hi = Vector((max(v.x for v in self.vertices), max(v.y for v in self.vertices), max(v.z for v in self.vertices)))
        return lo, hi

    def bvh(self) -> BVHTree:
        # epsilon=0: no padding, so contact is not mistaken for overlap or the reverse.
        return BVHTree.FromPolygons([v[:] for v in self.vertices], self.triangles, all_triangles=True, epsilon=0.0)


def depsgraph():
    return bpy.context.evaluated_depsgraph_get()


def world_mesh(obj, graph=None) -> WorldMesh:
    """Triangulated, evaluated, world-space mesh of a mesh-like object."""
    graph = graph or depsgraph()
    evaluated = obj.evaluated_get(graph)
    mesh = evaluated.to_mesh()
    try:
        mesh.calc_loop_triangles()
        matrix = evaluated.matrix_world
        vertices = [matrix @ v.co for v in mesh.vertices]
        triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    finally:
        evaluated.to_mesh_clear()
    return WorldMesh(obj.name, vertices, triangles)


def aabb_overlap(a: tuple[Vector, Vector], b: tuple[Vector, Vector], margin: float = 0.0) -> bool:
    """Broad phase only: True means 'test further', never 'penetrating' [non-penetration-contract.en.md]."""
    return all(a[0][i] - margin <= b[1][i] and b[0][i] - margin <= a[1][i] for i in range(3))


def set_frame(frame: int) -> None:
    scene = bpy.context.scene
    scene.frame_set(frame)
    bpy.context.view_layer.update()


def frame_list(start: int, end: int, step: int = 1) -> list[int]:
    """Sequential frames, always including the last one [non-penetration-contract.en.md 'complete range']."""
    if step < 1 or end < start:
        raise ValueError(f"bad frame range {start}..{end} step {step}")
    frames = list(range(start, end + 1, step))
    if frames[-1] != end:
        frames.append(end)
    return frames
