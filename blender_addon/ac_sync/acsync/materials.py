# SPDX-License-Identifier: GPL-3.0-or-later
"""Mapping Archicad surface names to the user's own Blender materials."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class MaterialMap:
    """Archicad surface name -> Blender material name, plus the naming rule for the rest.

    ``mapping`` holds the user's choices. Surfaces without an entry get a Blender material
    named ``prefix + surface name`` (created by the Blender layer if it does not exist).
    """

    mapping: dict[str, str] = field(default_factory=dict)
    prefix: str = ""

    def target_for(self, surface_name: str) -> str:
        mapped = self.mapping.get(surface_name)
        if mapped:
            return mapped
        return f"{self.prefix}{surface_name}"

    def is_mapped(self, surface_name: str) -> bool:
        return bool(self.mapping.get(surface_name))
