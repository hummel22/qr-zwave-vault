# Security Baseline (v1)

This document defines the minimum security controls for the v1 release of the QR Z-Wave Vault service.

## 1) Accepted authentication mode(s)

For v1, the service accepts **Bearer token authentication only** for API access.

- Required header: `Authorization: Bearer <token>`
- Any request without a valid bearer token must return `401 Unauthorized`.
- Basic authentication is **not** accepted in v1.

## 2) Session and cookie policy

v1 is API-token centric and does not require server-side browser sessions by default. If any cookie is introduced (for example, for browser tooling), it must meet all of the following constraints:

- `Secure=true` (cookie sent only over HTTPS)
- `HttpOnly=true` (not readable by client-side JavaScript)
- `SameSite=Strict` by default (`Lax` only when a documented flow requires it)
- Session cookies must be short-lived and not persisted longer than needed

## 3) CSRF expectations for browser forms

- If browser forms are introduced in v1, CSRF protection is required on all state-changing requests (`POST`, `PUT`, `PATCH`, `DELETE`).
- CSRF tokens must be user/session-bound, unguessable, and validated server-side.
- CSRF failures must return `403 Forbidden` without exposing validation internals.
- API requests authenticated by bearer token in the `Authorization` header (non-cookie auth) are not treated as browser-form CSRF flows.

## 4) GitHub token handling, redaction, and log scrubbing

### Token source (v1)

GitHub tokens must come from **environment variables only** in v1.

- Allowed source: `env` only
- Disallowed in v1: checked-in config files, plaintext local files, CLI arguments, or hardcoded constants

### Redaction rules

- Never print full tokens to logs, errors, traces, or metrics.
- Any token-like value must be redacted to a safe format (for example: first 4 chars + `…` + last 4 chars) only when absolutely required for debugging.
- Do not include authorization headers or credential-bearing URLs in logs.

### Log scrubbing

- Apply centralized log scrubbing for known secret patterns before emission.
- At minimum, scrub values associated with keys/headers such as `authorization`, `token`, `secret`, `password`, and `github_token`.
- Scrubbing must apply to both structured and plaintext logs.

## 5) Secret rotation and startup validation

### Rotation procedure

1. Generate a new credential in the upstream provider (for example, GitHub token).
2. Update deployment secret storage/environment with the new value.
3. Restart or roll the service so fresh processes read the new secret.
4. Validate health checks and authenticated API operations.
5. Revoke the old credential after successful cutover.

### Startup validation and errors

On startup, the application must validate required credentials and fail closed.

- Missing credential: terminate startup with a clear, actionable error (for example, missing required env var).
- Invalid format/value: terminate startup with a validation error.
- Error messages must identify *which variable* is invalid/missing, but must not reveal secret values.
- Service must not start in a degraded insecure mode when credentials are absent/invalid.
