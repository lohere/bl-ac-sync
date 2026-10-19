"""Compare an IFC export with what the Blender scene already holds, by Archicad GUID."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from .ifc_reader import Element


@dataclass(frozen=True)
class SceneRecord:
    """What a synced Blender object remembers about the element it came from."""

    guid: str
    element_id: str
    geometry_hash: str


@dataclass(frozen=True)
class Change:
    element: Element
    record: SceneRecord
    geometry_changed: bool
    label_changed: bool  # Element ID changed in Archicad


@dataclass
class Diff:
    added: list[Element] = field(default_factory=list)
    changed: list[Change] = field(default_factory=list)  # geometry and/or label
    unchanged: list[Element] = field(default_factory=list)
    removed: list[SceneRecord] = field(default_factory=list)

    @property
    def geometry_changed(self) -> list[Change]:
        return [c for c in self.changed if c.geometry_changed]

    def summary(self) -> str:
        return (
            f"{len(self.added)} added, {len(self.geometry_changed)} geometry changed, "
            f"{len(self.changed) - len(self.geometry_changed)} relabelled, "
            f"{len(self.unchanged)} unchanged, {len(self.removed)} removed"
        )


def compute_diff(
    elements: Mapping[str, Element],
    scene: Mapping[str, SceneRecord],
    detect_removed: bool = True,
) -> Diff:
    """``elements`` and ``scene`` are both keyed by Archicad GUID.

    With ``detect_removed`` off (a partial export, e.g. only the selection) an element missing
    from ``elements`` says nothing about Archicad, so nothing is reported as removed."""
    diff = Diff()
    for guid, element in elements.items():
        record = scene.get(guid)
        if record is None:
            diff.added.append(element)
            continue
        geometry_changed = record.geometry_hash != element.geometry_hash
        label_changed = record.element_id != element.element_id
        if geometry_changed or label_changed:
            diff.changed.append(Change(element, record, geometry_changed, label_changed))
        else:
            diff.unchanged.append(element)
    for guid, record in scene.items():
        if detect_removed and guid not in elements:
            diff.removed.append(record)
    return diff
