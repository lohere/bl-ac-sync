"""Mapping Archicad surface names to the user's own Blender materials."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_BLENDER_SUFFIX = re.compile(r"\.\d{3,}$")


def strip_blender_suffix(name: str) -> str:
    """'Brick - Aged Light.001' -> 'Brick - Aged Light' (Blender's duplicate suffix)."""
    return _BLENDER_SUFFIX.sub("", name)


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

    @classmethod
    def from_text(cls, text: str, prefix: str = "") -> MaterialMap:
        """Parse lines of ``Archicad surface = Blender material``; ``#`` starts a comment."""
        mapping: dict[str, str] = {}
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line or "=" not in line:
                continue
            left, right = (part.strip() for part in line.split("=", 1))
            if left and right:
                mapping[left] = right
        return cls(mapping=mapping, prefix=prefix)

    def to_text(self) -> str:
        return "\n".join(f"{k} = {v}" for k, v in sorted(self.mapping.items()))
