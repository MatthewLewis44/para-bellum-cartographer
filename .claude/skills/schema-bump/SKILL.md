---
name: schema-bump
description: Change the hex JSON schema that Unity consumes. Use when adding, renaming or removing an exported field, bumping SCHEMA_VERSION, or coordinating an artifact change with the Unity loader.
---

# Change the schema

`SCHEMA_VERSION` lives in `output/game_data_exporter.py`. It is the contract with a separate Unity
repository, and the contract is asymmetric.

## The loader's guard is directional

On the Unity side, `HexMap.ValidateSchemaVersion` accepts the same major version with an equal or
older minor and patch:

- an artifact **newer** than the loader → **throws** and refuses to load;
- an artifact **older** than the loader → logs and **silently defaults** the missing fields.

A refusal is loud and gets fixed in minutes. A silent default is a wrong number on a screen that
nobody questions. So the order matters:

1. **Additive only.** Add fields; do not remove or rename them after the fact. The schema's whole
   history is additive, and the loader depends on that.
2. **Bump the version in the same change as the field.** An artifact with a new field under an old
   version number is indistinguishable from one without it.
3. **Ship the pair together.** The exporter change and the Unity loader change are one coordinated
   landing. When the artifact is already staged in the Unity repository, **the artifact goes first** —
   a loader that lands first rejects the map everyone is working with.
4. **Tell the Unity side what an older artifact does** under the new loader, explicitly, in the
   commit message or the handoff note.

## After the bump

- Regenerate an artifact and check the field is actually populated, not merely declared.
- Run `validate_full_bbox.py` for the config you regenerated.
- Update `docs/hex-schema.md`, which is the field-level authority, and record the decision in
  `PARA_BELLUM_DECISIONS.md` if the change was a judgement call rather than a mechanical addition.
- Note that a deliberate non-export is not a defect. `rivers.scalerank` and `rivers.waterway_type`
  are withheld on purpose (AD-029) until river-crossing gameplay needs them.
