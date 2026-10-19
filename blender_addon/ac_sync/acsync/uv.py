# SPDX-License-Identifier: GPL-3.0-or-later
"""UV generation. IFC carries no texture coordinates, so textured materials need them made up.

Each face is projected onto its own plane, in world metres, so a texture keeps its real-world
scale and continues across coplanar faces and across neighbouring elements:

* wall-like faces (steeper than 45 degrees): U runs horizontally along the face, in the
  direction a viewer standing in front of it sees as "right"; V is the world height;
* floor-, ceiling- and roof-like faces: U follows the world X axis projected onto the face's
  plane, V is perpendicular to it in that plane (for a flat floor: world Y).

A ``scale`` of 1.0 means one UV unit per metre (a texture repeats once per metre in Blender's
default mapping); a larger value makes textures bigger.
"""

from __future__ import annotations

import math

_WALL_LIMIT = 0.7071  # |normal.z| below this: wall-like (face steeper than 45 degrees)


def _face_normal(vertices, corner_indices):
    """Unit normal by Newell's method; ``None`` for degenerate faces."""
    nx = ny = nz = 0.0
    count = len(corner_indices)
    for k in range(count):
        a = corner_indices[k] * 3
        b = corner_indices[(k + 1) % count] * 3
        ax, ay, az = vertices[a], vertices[a + 1], vertices[a + 2]
        bx, by, bz = vertices[b], vertices[b + 1], vertices[b + 2]
        nx += (ay - by) * (az + bz)
        ny += (az - bz) * (ax + bx)
        nz += (ax - bx) * (ay + by)
    length = math.sqrt(nx * nx + ny * ny + nz * nz)
    if length < 1e-12:
        return None
    return nx / length, ny / length, nz / length


def box_projection_uvs(
    vertices: tuple[float, ...],
    face_sizes: tuple[int, ...],
    face_indices: tuple[int, ...],
    scale: float = 1.0,
) -> tuple[float, ...]:
    """Flat ``u, v`` pairs, one per face corner, in the order of ``face_indices``."""
    if scale <= 0:
        raise ValueError("UV scale must be positive")
    inv = 1.0 / scale
    out: list[float] = []
    position = 0
    for size in face_sizes:
        corners = face_indices[position:position + size]
        position += size
        normal = _face_normal(vertices, corners) if size >= 3 else None
        if normal is None:
            out.extend([0.0, 0.0] * size)
            continue
        nx, ny, nz = normal
        if abs(nz) < _WALL_LIMIT:
            # t = up x n: "right" for someone looking at the face from outside; b = up
            tl = math.hypot(nx, ny)
            tx, ty = -ny / tl, nx / tl
            for c in corners:
                i = c * 3
                out.append((vertices[i] * tx + vertices[i + 1] * ty) * inv)
                out.append(vertices[i + 2] * inv)
        else:
            # t = world X projected onto the face plane, b = n x t
            dot = nx  # X . n
            tx, ty, tz = 1.0 - nx * dot, -ny * dot, -nz * dot
            tl = math.sqrt(tx * tx + ty * ty + tz * tz)
            tx, ty, tz = tx / tl, ty / tl, tz / tl
            bx, by, bz = ny * tz - nz * ty, nz * tx - nx * tz, nx * ty - ny * tx
            for c in corners:
                i = c * 3
                x, y, z = vertices[i], vertices[i + 1], vertices[i + 2]
                out.append((x * tx + y * ty + z * tz) * inv)
                out.append((x * bx + y * by + z * bz) * inv)
    return tuple(out)
