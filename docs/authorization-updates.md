# Authorization file and updates

The current file shape is preserved exactly:

```csv
KEY,NAME,ALLOW
0123456789,Example A,Y
ABCDEF0123,Example B,N
```

`KEY` is the ten-character uppercase hexadecimal data field produced by the
reader. `NAME` must be a non-empty administrative label and is never copied to
audit output. `ALLOW` is `Y` or `N` (case-insensitive and normalized on load).
Headers, record shape,
types, key uniqueness, and additional columns are validated against
`schemas/authorization-file/schema.json`.

A candidate is parsed and validated completely before installation. A valid
candidate is written to a temporary sibling, flushed and fsynced, then
installed with `os.replace`; readers therefore see the old or new file, never
a partial write. Invalid candidates are rejected before replacement. Reload
updates the in-memory set only after validation, so failure retains the last
valid set.

Fixtures are sanitized and contain no production identifiers or names.
