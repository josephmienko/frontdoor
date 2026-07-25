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

Keys may be exactly ten hexadecimal characters or five two-character groups
separated by hyphens. Both forms normalize to uppercase unhyphenated form;
irregular or leading/trailing hyphens are invalid. Empty, header-only,
malformed-quoted, BOM-altered, and zero-record candidates are rejected. This
prevents an accidental empty update from silently removing every authorized
user.

A candidate is parsed and validated completely before installation. A valid
candidate is written to a temporary sibling, flushed and fsynced, then
installed with `os.replace`; readers therefore see the old or new file, never
a partial write. Invalid candidates are rejected before replacement. Reload
updates the in-memory set only after validation, so failure retains the last
valid set. Installation stages expose typed failure records for local audit
integration. Failed temporary files are removed when possible, and abandoned
sibling temporary files from an interrupted process are removed before the
next installation.

Fixtures are sanitized and contain no production identifiers or names.
