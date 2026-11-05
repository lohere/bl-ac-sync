# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) once it
reaches 1.0; until then minor versions may change behaviour.

## Unreleased (0.2.0 work, branch `feature/two-way-morphs`)

- **New, experimental:** *bl-ac morphs*, a second panel that sends Blender meshes to Archicad as
  Morphs and brings Archicad morphs into Blender, both ways, with materials, conflict detection and
  a dry run. Needs Tapir 1.8.0 or newer. See [docs/MORPHS.md](docs/MORPHS.md).

## 0.1.1

- **New:** a *Help* button next to the version in the panel (Advanced) opens the project page with
  the instructions.

## 0.1.0 (first public release)

- **Fixed:** syncing only the Archicad selection or one story no longer marks every other synced
  object as removed (and, with the default policy, moves it to the Removed collection). Partial
  exports skip removal detection and the report says so.
- **New:** *Select changed* button and *Select changed objects* option: after a sync or dry run,
  select the objects that were added or changed.
- **New:** the completeness check names Building Materials that are excluded from collision
  detection (typically the ground) as the reason elements or terrain are missing, in a red box in
  the panel.
- **New (opt-in):** *Include excluded materials* switches the collision-detection flag on for just
  those materials during an export, exports again, and restores the original settings.
- The IFC translator is chosen from a dropdown filled from Archicad (Check Archicad loads the
  list). Every setting and button now has a tooltip; a test keeps it that way.
- **New:** walls that inherit the adjoining wall's end surface (Archicad's default) no longer come
  out with striped ends. The IFC export ignores that setting, so the add-on applies the rule itself
  using the wall connections from Tapir (Output > *Match wall end finishes*, on by default).
- Stairs and railings are tested against a real Archicad export.
- GPL-3.0-or-later license added.
- **Renamed** to **bl-ac sync** (sidebar tab "bl-ac"). The default collection is now `bl-ac sync`
  (was `AC_Sync`); an existing scene that used the old name needs its collection renamed, or the
  *Collection* setting changed back. The extension id stays `ac_sync`.

## 0.0.1

First working version: Archicad to Blender sync by element GUID, dry run, material mapping,
UV generation, completeness check.
