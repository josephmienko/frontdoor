# Authorization fixture contracts

All identifiers and names in this directory are synthetic.

| Fixture | Contract |
|---|---|
| `valid.csv` | minimal authorized and disabled legacy-shape records |
| `valid_multiple_records.csv` | multiple records, hyphen normalization, lowercase allow values |
| `replacement.csv` | valid atomic replacement candidate |
| `duplicate.csv` | duplicate normalized key is rejected |
| `malformed.csv` | malformed key is rejected |
| `invalid_header_only.csv` | zero-record candidate is rejected |
| `invalid_malformed_quoting.csv` | strict CSV parsing rejects unclosed quoting |
| `invalid_bom.csv` | a BOM-altered header is rejected rather than silently reinterpreted |
