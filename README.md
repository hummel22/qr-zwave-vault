# QR Z-Wave Vault

## Security assumptions

This project is designed for a trusted homelab environment with explicit trust boundaries:

- The internal LAN and host environment are assumed to be controlled by the operator.
- Untrusted clients and networks are outside the homelab trust boundary and must be treated as hostile.
- Administrative endpoints and secret-bearing workflows should remain reachable only from trusted segments.

### HTTPS requirement

All authenticated traffic must use HTTPS in transit.

- Do not deploy with plaintext HTTP for token-bearing or session-bearing requests.
- If TLS termination is handled by a reverse proxy, the proxy-to-app path must still be protected within the trusted network boundary.
- Cookies (if used) must be configured with `Secure` and aligned with the baseline in `docs/security/baseline.md`.
