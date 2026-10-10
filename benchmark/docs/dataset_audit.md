# Dataset audit — MasterOne G1

Audit time (UTC): `2026-09-30T09:25:35.510074+00:00`

## Internal MasterOne test data

Raw files were read-only. The manifest records their SHA-256, UTF-8 encoding, schema and row counts.

| File | Rows | Columns | SHA-256 |
| --- | ---: | ---: | --- |
| `datasets/masterone_test/records.csv` | 4,213 | 11 | `16587b35766a107c4deb1ff4e237f122a954b9b27f53c15ff12fcc71ec83b046` |
| `datasets/masterone_test/pairs.csv` | 1,200 | 9 | `4272fe4be8e06cebcf18abf6f1bd88f63de0dc854d83175a155d465c8f1746be` |

Structural audit status: **PASS**.

### Dataset profile

| Check | Observed |
| --- | --- |
| Sources | `{'BILL': 1407, 'CRM': 1408, 'ERP': 1398}` |
| Distinct non-empty entity labels | 4213 record rows across 1200 entity IDs |
| Pair labels | `{'negative': 240, 'positive': 960}` |
| Pair split | `{'test': 1200}` |
| Pair source slices | `{'BILL|BILL': {'negative': 16}, 'BILL|CRM': {'negative': 25, 'positive': 322}, 'BILL|ERP': {'negative': 85, 'positive': 329}, 'CRM|CRM': {'negative': 11, 'positive': 1}, 'CRM|ERP': {'negative': 63, 'positive': 307}, 'ERP|ERP': {'negative': 40, 'positive': 1}}` |
| Pair case types | `{'HARD_NEGATIVE': 240, 'MIXED_ADMIN': 114, 'NOISY_DUPLICATE': 300, 'NORMAL_DUPLICATE': 420, 'OLD_NEW_ADDRESS': 126}` |
| Hard negative types | `{'<empty>': 960, 'RELATED_ADMIN_UNITS': 39, 'SAME_ADDRESS_DIFFERENT_PERSON': 38, 'SAME_NAME_DIFFERENT_ADDRESS': 41, 'SAME_SURNAME_SAME_COMMUNE': 40, 'SIMILAR_PHONE': 41, 'SIMILAR_STREET': 41}` |
| Entity/source groups with repeated records | 617 |
| Entity count per entity/source group | `{'1': 2976, '2': 614, '3': 3}` |
| Duplicate record IDs | 0 |
| Duplicate / reversed pair keys | 0 / 0 |
| Self-pairs / dangling rows / invalid labels | 0 / 0 / 0 |
| Duplicate-key label conflicts | 0 |
| Pair label vs entity_id consistency conflicts | 0 |

### Leakage controls

Feature allowlist: `address, email, name, phone`.
Raw ID is only a join/trace key and is never tokenized or compared. `entity_id`, `address_id`, 
`noise_type`, `difficulty`, `temporal_case`, and pair annotations are retained in gold/evaluation 
structures, not in scorer-facing `Record` or `RecordFeatures`.

Columns present in records that require gold/evaluation isolation: `address_id, difficulty, entity_id, noise_type, temporal_case`.
Columns present in pairs that require gold/evaluation isolation: `case_type, difficulty, entity_a, entity_b, hard_negative_type, is_match, split`.

### Split finding

All listed pair rows currently use the `test` split. There is no independent train or dev partition 
in this dataset. Do not split these test pairs for training/tuning; such experiments are BLOCKED.
The presence of `entity_id` values is generator-provided and is not treated as independently verified complete entity gold.

## Local address references

Reference bundle version: `local-reference-sha256:abb0d846d30fffd53cebea3d13aa2f5ba0e1273aad4d76464114ddc82edea463`.
The `.bak` backup was not loaded. Column aliases and administrative aliases are separate tables; 
unit rows and old-to-new mappings are separate. Ambiguous mappings preserve all candidate units.

Rows: column aliases 26; administrative aliases 66,629; 
units 13,349; old-to-new mappings 10,565.
Mapping status counts: `{'ambiguous': 1101, 'resolved': 9464}`.
Reference integrity issue counts: duplicate unit keys 0, 
dangling parents 0, missing mapping sources 0, 
missing mapping targets 0, duplicate mappings 0, 
missing alias units 0, 
administrative alias collisions 5808.

Column alias collisions 0; invalid mapping dates 0.
Mapping directions: `{'old_to_new': 10565}`; target count distribution: `{'1': 10565}`.
Upstream source/license provenance for local reference files has not been independently verified.

Detailed hashes, schemas and integrity findings are in `datasets/manifests/`.
