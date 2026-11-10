# Field Modification and Prisma modify_inp

Use this reference for any edit, target-resolution problem, or Prisma `modify_inp` scenario.

## Resolve a Target

Search only returns fields on final effective cards:

```powershell
python scripts/relap5_inp.py search input.i "20504000 tbp"
```

Each hit includes `target`, `value`, `line_no`, `field_line_no`, and `category`. Preserve the target exactly. Shared parser semantics:

- `CARD` is normalized for leading zeroes.
- The keyword is textual, or `-` when the card has no textual first field.
- The field ordinal is one-based and includes fields from valid continuations.
- The source line is the logical data card's `line_no`, not the output line where a continuation field sits.

If no hit appears, check the card number, whether the card was overridden/deleted, whether parsing reported `unparsed` lines, and whether the query is too restrictive. Do not switch to an arbitrary textual replacement.

## CLI Preview and Apply

Preview is the default and is the safest way to inspect an edit:

```powershell
python scripts/relap5_inp.py modify input.i `
  --target 0000100:new:2@2 `
  --value transnt `
  --expected stdy-st
```

This command must return with `applied: false`, `dry_run: true`, and a diff; the input file remains byte-for-byte unchanged. Use JSON for scripted checks:

```powershell
python scripts/relap5_inp.py modify input.i `
  --target 0000100:new:2@2 --value transnt --expected stdy-st --json
```

Write only after reviewing the diff:

```powershell
python scripts/relap5_inp.py modify input.i `
  --target 0000100:new:2@2 --value transnt --expected stdy-st --apply
```

On success, `applied` is true and the file is atomically replaced after these checks:

- Target exists on a final effective card and keyword/field/source line agree.
- Optional `--expected` matches the current logical value.
- The new value fits the field type and remains on a physical line no longer than 80 columns.
- The candidate parses with the same field count, same non-target field values, and the same target field's physical line.
- The candidate round-trips in the original encoding, and another process has not changed the file during the edit.
- The written file re-parses to the intended value.

The editor preserves untouched characters, whitespace, comments, encoding/BOM, and newline style. It does not recreate cards or append missing fields. Do not fall back to search-and-replace when a target fails.

## Prisma modify_inp

`modify_inp` takes a target and a new value. It calls the same field editor with immediate write enabled:

```text
ACTION       OBJECT       PARAMETER 1          PARAMETER 2
modify_inp   input.txt    100:new:2@5          transnt
```

Example full row:

```text
TEST-R501     modify_inp   input.txt    100:new:2@5     transnt
```

Resolution is relative to the configured case run directory; `object` should name the deck in that run directory. The target must be generated from the effective card in that exact deck. Before a high-impact change, use the CLI dry-run against a copy of that deck, then verify the Prisma run directory is the intended one. `modify_inp` has no separate expected-value parameter; if an old-value guard is important, perform a CLI edit with `--expected` first or verify the target before launching the script.

## Troubleshooting

| Symptom | Check |
|---|---|
| Target not found | `@SOURCE_LINE` must be the logical card start, not a continuation line. Parse the current file again. |
| Card is overridden | Search never returns a superseded card. Locate the final occurrence. |
| Card is deleted | A bare card number removes the earlier card. A later deletion makes the card non-editable. |
| Keyword mismatch | For a numeric-only card use `-`; textual cards require the current textual keyword. |
| Field out of range | Count all valid continuation fields; field numbers are one-based. |
| Numeric-value error | The existing field is numeric, so the replacement must remain numeric. |
| Unparsed continuation | Remove the intervening blank/comment/title/terminator only if that is a deliberate deck-layout edit; otherwise fix the deck structure first. |
| Line exceeds 80 columns | Shorten the replacement or adjust the physical layout deliberately. Do not rely on columns after 80. |
| Concurrent-change error | Re-read/re-parse the deck; do not force overwrite. |
| Prisma step fails | Verify run-directory-relative `object`, exact target, and current deck content. |

## Verification Recipe

1. Copy the target deck to a temporary location when testing.
2. Run `search` and save the selected target/value.
3. Run `modify` without `--apply`; assert the temporary file is unchanged.
4. Apply with `--expected` and check `applied: true`.
5. Re-parse; assert the target value changed and `unparsed_lines`/field counts did not regress.
6. Compare file bytes/decoding for unchanged encoding, BOM, and line endings.