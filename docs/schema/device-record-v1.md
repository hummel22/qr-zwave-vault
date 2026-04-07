# Device Record Schema Contract (v1)

This document defines the canonical schema contract for persisted Z-Wave device records.

## Contract metadata

- **Schema name:** `device-record`
- **Schema version value:** `"1"`
- **Record media type:** JSON object
- **Storage filename rule:** `{id}.json`

## Field definitions

### Required fields

| Field | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `schema_version` | string | Must equal `"1"` | Enables forward-compatible migrations and parsing logic. |
| `id` | string | Lowercase slug matching `^[a-z0-9][a-z0-9-]{1,62}[a-z0-9]$` | Canonical stable identifier used for filename and lookups. |
| `device_name` | string | 1-120 chars, trimmed, non-empty | Human-readable display label. Not used as canonical key. |
| `raw_value` | string | Trimmed, non-empty | Original onboarding payload or normalized source token for dedupe. |
| `dsk` | string | Trimmed, non-empty, normalized format for storage | Device-Specific Key used for secure inclusion and dedupe. |

### Optional fields

| Field | Type | Constraints | Notes |
| --- | --- | --- | --- |
| `location` | string \| null | <= 120 chars when set | Human location hint (e.g., "Kitchen"). |
| `description` | string \| null | <= 500 chars when set | Free-form operator notes. |
| `manufacturer` | string \| null | <= 120 chars when set | Vendor-provided manufacturer label. |
| `model` | string \| null | <= 120 chars when set | Vendor model label/number. |
| `created_at` | string \| null | ISO-8601 datetime when set | Server-managed creation timestamp. |
| `updated_at` | string \| null | ISO-8601 datetime when set | Server-managed update timestamp. |
| `metadata` | object \| null | JSON object if set | Extensible non-indexed attributes. |

> Additional keys SHOULD be rejected by API validation in strict mode to keep contract drift visible.

## Canonical `id` generation rule

`id` MUST be generated independently of `device_name`.

1. Normalize `raw_value` and `dsk` (trim whitespace; canonicalize DSK separator format).
2. Build source key: `"{normalized_raw_value}::{normalized_dsk}"`.
3. Compute SHA-256 digest of source key.
4. Encode digest as lowercase hex.
5. Set `id = "dev-" + digest[0:20]` (20 hex chars).

### Rationale

- Stable across display-name edits.
- Deterministic across imports/environments.
- Avoids collisions tied to human-friendly names.

## Uniqueness constraints

These uniqueness constraints are required at persistence layer and enforced in API validation:

1. `id` is globally unique.
2. `raw_value` is globally unique after normalization.
3. `dsk` is globally unique after normalization.

If any uniqueness constraint is violated, API must return a validation error that identifies the conflicting field.

## Filename and rename behavior

- Persist each device record as: `{id}.json`.
- `device_name` changes MUST NOT alter `id`.
- `device_name` changes MUST NOT trigger file rename.
- File rename is only allowed when `id` changes due to an explicit migration process (not normal patch/update).

## Validation and error contract (FastAPI model layer)

The FastAPI model layer (e.g., `app/models/device.py`) should reject invalid payloads with explicit field-scoped errors, including:

- Missing required field.
- Unsupported `schema_version`.
- Invalid `id` format.
- Blank/whitespace-only `raw_value` or `dsk`.
- Uniqueness conflicts for `id`, `raw_value`, or `dsk`.
- Attempt to mutate immutable identity fields (`id`, `raw_value`, `dsk`) in update routes.

Recommended API error shape:

```json
{
  "detail": [
    {
      "loc": ["body", "dsk"],
      "msg": "dsk must be unique; value already exists",
      "type": "value_error.conflict"
    }
  ]
}
```
