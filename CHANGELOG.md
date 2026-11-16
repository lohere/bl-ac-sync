# Changelog

All notable changes. The project follows [Semantic Versioning](https://semver.org/) once it
reaches 1.0; until then minor versions may change behaviour.

## 0.2.1

- **Blender 5.3** is supported (automated tests pass on 5.3.0).
- **Morphs and the main Sync work together.** Archicad's morphs are no longer read from the IFC (option
  *Sync morphs both ways*, on by default, needs Tapir 1.8.0 or newer). Sync brings them into the morph
  collection and syncs them both ways there, so changes made in Blender go back to Archicad, and meshes
  made in Blender are no longer duplicated by the IFC. Morphs an older version imported as plain objects
  are taken over on the first sync.
- **New:** *Apply the dry run* button (both panels) carries out what a dry run showed, without exporting
  from Archicad again.
- **New:** a *bl-ac project* panel holds what both tools share: the connection to Archicad, the model
  offset and the port. Archicad is looked for when Blender starts and with every sync, so the panel no
  longer says "not checked" while the tool works.
- The *Removed* collection starts hidden, and the *Last result* blocks can be folded away.
- **Faster:** reading the Archicad export uses more processor cores (8 instead of 4 at most), about 30 %
  faster on a 450-element project. The report now has a *Time* line that shows where a sync spent its time.

## 0.2.0

- **New, experimental:** *bl-ac morphs*, a second panel that sends Blender meshes to Archicad as
  Morphs and brings Archicad morphs into Blender, both ways, with materials, conflict detection and
  a dry run. Needs Tapir 1.8.0 or newer. See [docs/MORPHS.md](docs/MORPHS.md).
  Warped faces are split into triangles, because Archicad faces must be flat; a copy of a pushed
  object is skipped until it is detached.
- **New:** *Sync collection* (two-way sync of a chosen Blender collection in one click) and visible
  *Selected* / *All* buttons for Push and Pull (Pull Selected takes the morphs selected in Archicad).

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
