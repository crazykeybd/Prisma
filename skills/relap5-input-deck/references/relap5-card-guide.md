# RELAP5 / SCDAP Input-Deck Guide

This is a working guide distilled from the supplied Chinese RELAP5 input-card manual and the parser's supported behavior. It is not a complete card catalogue; use the card number and manual for the physical meaning of a field.

## Record Layout

- RELAP5 reads records of up to 96 characters but uses **only columns 1–80**. Treat columns 81+ as non-data. Never place a meaningful field, continuation, or terminator there.
- A line may contain whitespace-separated or comma-separated fields. Leading and trailing spaces are ignored.
- The parser also accepts these practical deck forms: 3–8 digit card numbers, 7/8 digit RELAP5-3D cards, three-digit small-edit cards, and an external listing line number before a card. An unrecognized non-empty line is kept as `unparsed`; do not assume it is harmless.
- Keep each field intact on one physical record. A token may not start on one card and finish on the next.

## Card Kinds

| Form | Meaning | Effective behavior |
|---|---|---|
| `=TITLE` | Title card | Last title in the input wins. The title can be long; the rest of the line is text, not fields to edit. |
| `* comment` or `$ comment` | Comment card | Printed only; ignored as data. Blank lines are also comments. Do not place a comment between a data card and its continuation. |
| `CARD fields...` | Data card | Card identifier is an unsigned integer; subsequent values are its fields. |
| `+ fields...` | Continuation | Extends the immediately preceding data card; it has no card number. |
| `/` or `.` first non-space | Terminator | `/` (truncation) preserves the prior card set for a following problem; `.` clears prior input before the next problem. |
| `.RELAP END CARD` | End marker | Recognized by the parser as an end card. A missing marker is reported as a warning. |
| `CARD` with no fields | Deletion | Deletes the previous data card with the same normalized card number. Deleting an absent card is not an error. A deletion also removes the card's continuations. |

## Replacement Semantics

Process cards in source order:

1. A later card with the same **normalized** card number replaces the earlier card. Leading zeroes are insignificant: `100` and `0000100` are the same identifier.
2. A bare card number deletes the current card and its continuation data.
3. Only the final effective card can be searched or modified. Earlier occurrences remain in parse results as auditing records but must not be edited.
4. If a replacement occurs, a later continuation attaches to that replacement only.

Do not modify an earlier duplicate merely because its text looks familiar. Search returns effective targets and includes source line numbers precisely to avoid this ambiguity.

## Fields and Values

- Fields are separated by whitespace or commas. A quoted field may contain spaces and commas; use matching single or double quotes. A doubled quote (`''` or `""`) represents a literal quote inside the field.
- Numeric fields accept ordinary numbers and RELAP5/Fortran implicit exponents, for example `1.0e-6`, `1.0-6`, and `0.1245+2`. Do not change a numeric field to text.
- An unquoted replacement must be a single field: no embedded spaces, commas, quotes, or newlines. A quoted replacement remains quoted, with the original quote style preserved.
- Quotes are part of the physical token but not the logical value. Do not strip them from the source line unless you are deliberately changing the field's type.
- Alphanumeric parameters are normally limited to eight characters for portability. Preserve the manual's card-specific limits; the parser does not validate every physical range.

## Continuations

A continuation starts with `+` as the first non-space character and extends the immediately preceding data card or continuation:

```text
1000101  1.0  2.0
+  3.0  4.0
```

The fields become fields 1–4 of the logical card `1000101`. A target can therefore point to a field whose `field_line_no` is the continuation's physical line while `@SOURCE_LINE` remains the data card's start line.

A continuation is invalid if a blank line, comment card, title card, terminator, or other non-data card intervenes. The parser records such a line as `unparsed` with an explanation. Do not "repair" it by guessing which card it belonged to.

## Field Targets

The stable target format is:

```text
CARD:KEYWORD:FIELD@SOURCE_LINE
```

- `CARD` is matched by normalized number, so leading-zero variants are equivalent.
- `KEYWORD` is the card's textual first field when available. For a pure numeric card, use `-`, not the current first value.
- `FIELD` is one-based and counts across the original card plus all valid continuations.
- `SOURCE_LINE` is the physical line number where the logical data card begins, not a field's continuation line.

Examples:

```text
0000100:new:2@2
1000101:-:1@10
20504000:tbp area:1@2
```

For `1000101` in a typical single-volume card, fields 1–3 are flow area, length, and volume. Changing one does not automatically preserve physical consistency; check related geometry fields when appropriate.

## Card Families

The parser classifies cards for navigation; it does not replace the manual's field definitions.

| Pattern | Typical family |
|---|---|
| `0000xx` / `100`–`147` control/problem cards | Problem options, time controls, trips, control systems |
| `CCCxxxx` with hydraulic component number `100`–`147` | Hydraulic components and junctions |
| `1CCCGXNN` | Heat structures |
| `201...`, `202...` | Thermal properties, general tables |
| `203...`, `204...` | Plot requests |
| `205...` | Control-system cards |
| `206...` | Trips and protection |
| `300...` | Point kinetics |
| `6...` | Radiation |

These are routing aids for finding a card, not permission to edit an arbitrary numeric match. Verify the card's intended field in the manual and use the parser's returned target.

## Practical Inspection Checklist

1. Parse and review `summary.warnings`, `unparsed_lines`, `overridden_cards`, `deleted_cards`, and `has_end_card`.
2. Search a narrow query and confirm card number, keyword, field ordinal, value, and source line.
3. Preview the edit; inspect the changed physical line and unified diff.
4. Apply, then parse again and confirm the intended field changed while surrounding fields and line layout remained intact.