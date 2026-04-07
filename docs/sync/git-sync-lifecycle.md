# Git Sync Lifecycle

## Purpose

Define a deterministic, observable sync lifecycle for create/update/delete operations so local state and the Git remote stay convergent while exposing actionable status to operators and UI.

## Scope

This lifecycle applies to all mutating sync operations:

- `create`
- `update`
- `delete`

Read-only operations (status checks, health probes) do not execute this lifecycle.

## Canonical Operation Order

All mutating operations MUST execute the following ordered stages:

1. `pull`
2. `validate`
3. `write`
4. `commit`
5. `push`

### Stage Requirements

| Stage | Required behavior | Failure class |
| --- | --- | --- |
| `pull` | Fetch and integrate latest remote branch state before local mutation. | network/conflict |
| `validate` | Re-evaluate request against current HEAD and domain invariants. | validation/conflict |
| `write` | Apply deterministic file-system mutation for create/update/delete. | io/validation |
| `commit` | Create exactly one commit for this operation if and only if there is a material diff. | git/idempotency |
| `push` | Publish commit to remote branch. | network/auth/conflict |

### Why this order is fixed

- Prevents writing based on stale remote state.
- Ensures validation reflects latest merged view.
- Restricts commit creation to post-validated deterministic changes.
- Makes retries safe by re-entering from `pull` with idempotency guards.

## Retry Policy

Retries are permitted for transient failures in `pull` and `push` (and optionally lock-contention in `commit`).

### Policy

- `max_attempts`: `5` total attempts (initial try + up to 4 retries)
- Backoff: exponential with base delay `500ms`
- Jitter: full jitter in `[0, computed_backoff]`
- Cap: `max_backoff_ms = 30_000`

### Formula

For attempt index `n` (1-based):

- `computed_backoff_ms = min(max_backoff_ms, base_delay_ms * 2^(n-1))`
- `sleep_ms = random(0, computed_backoff_ms)`

### Non-retriable failures

Do **not** retry when:

- validation fails (`validate` stage)
- conflict is deterministically reproducible and requires user intent (`delete` on already-modified record without force semantics)
- auth/permission failures unlikely to self-heal in retry window

## Offline Queue Behavior (Push Failure)

When `push` fails after all retry attempts:

1. Persist operation in local offline queue.
2. Mark queue item with failure metadata and next retry timestamp.
3. Keep local commit intact; do not rewrite history.
4. Surface state as `blocked` if no background dispatcher is running, otherwise `retrying`.

### Queue semantics

- Ordering: FIFO by `enqueued_at`.
- De-duplication key: `operation_id` + `target_resource_id` + `target_revision`.
- Persistence: durable storage (survives process restart).
- Dispatch trigger: connectivity restored, periodic scheduler tick, or explicit user action.

### Queue drain behavior

- Before replaying an item, rerun lifecycle from `pull`.
- If item becomes no-op after reconciliation, mark as `clean` and drop.
- If replay encounters deterministic conflict, mark item `conflicted` and halt dependent items for same resource.

## Idempotency and Duplicate Commit Prevention

### Idempotency rules

Each operation MUST carry a stable `operation_id` (UUID/ULID). Replays with the same `operation_id` must not produce additional semantic writes.

Idempotency checks:

- Skip `write` if desired end-state already matches working tree.
- Skip `commit` if `git diff --quiet` indicates no material change.
- Record `(operation_id, resulting_commit_sha | no_op)` in idempotency ledger.

### Duplicate commit prevention

The system MUST prevent multiple commits for a single logical operation by enforcing:

- at-most-once commit creation per `operation_id`
- commit message trailer (example): `Sync-Operation-Id: <operation_id>`
- pre-commit scan of recent local commits for same trailer and identical tree hash

If duplicate is detected:

- treat as successful replay
- attach existing commit SHA to operation result
- continue to `push` only if that commit is not already reachable from remote

## Conflict Handling Outcomes for UI

Sync must expose one of these canonical UI states:

- `clean`: operation completed and remote is in-sync
- `retrying`: transient failure, automatic retries/queue replay in progress
- `conflicted`: user action required to resolve semantic or merge conflict
- `blocked`: sync cannot proceed due to persistent non-transient issue

### State mapping guidance

| Condition | UI state |
| --- | --- |
| Lifecycle succeeded through `push` | `clean` |
| In retry window (attempt < max_attempts) | `retrying` |
| Merge conflict or domain conflict requiring decision | `conflicted` |
| Retries exhausted, auth failure, missing credentials, offline queue stalled | `blocked` |

## Observable Status Contract

Expose structured fields for both `/health` and `/api/sync/status`.

## `/health` (service-level summary)

```json
{
  "status": "ok|degraded|down",
  "sync": {
    "state": "clean|retrying|conflicted|blocked",
    "last_success_at": "RFC3339 timestamp",
    "last_attempt_at": "RFC3339 timestamp",
    "consecutive_failures": 0,
    "queue_depth": 0,
    "oldest_queue_age_ms": 0,
    "blocked_reason": "string|null"
  }
}
```

### `/health` expectations

- `status=ok` when sync state is `clean` and queue depth is within SLO threshold.
- `status=degraded` when state is `retrying` or queue depth/age breaches threshold.
- `status=down` when state is `blocked` or unrecoverable sync subsystem failure occurs.

## `/api/sync/status` (operation-level detail)

```json
{
  "state": "clean|retrying|conflicted|blocked",
  "current_operation": {
    "operation_id": "string",
    "type": "create|update|delete",
    "stage": "pull|validate|write|commit|push",
    "attempt": 1,
    "max_attempts": 5,
    "started_at": "RFC3339 timestamp"
  },
  "retry": {
    "eligible": true,
    "next_retry_at": "RFC3339 timestamp|null",
    "backoff_ms": 0,
    "jitter_ms": 0
  },
  "queue": {
    "depth": 0,
    "oldest_enqueued_at": "RFC3339 timestamp|null",
    "items": [
      {
        "operation_id": "string",
        "resource_id": "string",
        "state": "retrying|conflicted|blocked",
        "last_error_code": "string",
        "last_error_message": "string",
        "last_attempt_at": "RFC3339 timestamp"
      }
    ]
  },
  "last_result": {
    "operation_id": "string",
    "outcome": "success|no_op|conflict|failed",
    "commit_sha": "string|null",
    "error_code": "string|null",
    "error_message": "string|null",
    "finished_at": "RFC3339 timestamp"
  }
}
```

### Status field requirements

- Fields MUST be present with `null` where unknown (avoid shape drift).
- `error_code` should be stable, machine-parseable, and documented.
- Timestamps MUST be RFC3339 UTC.
- `state` transitions MUST be monotonic per attempt path (no silent regressions).

## Suggested Error Codes

- `SYNC_NETWORK_TIMEOUT`
- `SYNC_REMOTE_REJECTED`
- `SYNC_AUTH_FAILED`
- `SYNC_VALIDATION_FAILED`
- `SYNC_MERGE_CONFLICT`
- `SYNC_IDEMPOTENCY_VIOLATION`
- `SYNC_QUEUE_PERSISTENCE_FAILED`

## Acceptance Checklist

- [ ] All mutating paths enforce `pull -> validate -> write -> commit -> push`.
- [ ] Retry logic uses configured attempts/backoff/jitter.
- [ ] Push exhaustion enqueues durable offline work.
- [ ] Idempotency ledger prevents duplicate commits.
- [ ] UI receives one of `clean|retrying|conflicted|blocked` for every operation.
- [ ] `/health` and `/api/sync/status` expose required structured fields.
