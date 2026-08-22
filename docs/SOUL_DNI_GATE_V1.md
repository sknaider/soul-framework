# SOUL DNI Runtime Gate v1

## Contract

A persistent SOUL Core or SOUL Platform runtime must not open its database,
proxy, or MCP surface until it verifies a credential issued by the SOUL
Identity Authority (SIA).

The SIA signs two domain-separated documents: the DNI credential and the
trust/revocation snapshot. Core pins the SHA-256 fingerprint of SOUL's public
root in its runtime bytes; replacing the JSON files, their configuration
digest, or deleting a revocation cannot introduce a new issuer or make a
denial permissive without the SOUL private root.

The runtime verifies, in this order:

1. protected regular credential and trust-store files, with no symlink path;
2. a trust-store SHA-256 pin delivered by the authenticated installer;
3. built-in SOUL root fingerprint and Ed25519 signatures over both documents;
4. canonical `urn:soul:agent:<UUIDv7>` identity;
5. `active` lifecycle and absence from the revocation set;
6. runtime audience (`soul-core` or `soul-platform`);
7. issue/expiry times and a maximum 30-day renewable credential lifetime;
8. a credential-signed `trust_sequence` no newer than the active signed trust
   generation, preventing a new credential from replaying an older CRL;
9. immutable `machine_soul_id` and live OS-machine-plus-owner binding.

Any missing or invalid element fails closed before persistent state is opened.
An in-memory Core instance remains available only as non-persistent scratch
state for hermetic tests; it cannot embody or preserve a SOUL identity.

## Authority boundary

The runtime wheel contains verification only. It never contains or generates
the SIA private key. The repository's operational `tools/soul_dni_sia.py`
requires an existing externally custodied Ed25519 key and emits only:

- `soul-dni.json` — signed, renewable device credential;
- `soul-dni-trust.json` — signed public issuer keys and revocation snapshot;
- a receipt with the trust-store SHA-256 pin.

The authenticated installer must deliver the two files and pin together. A
bare UUID, a self-written TOML field, or a model/API credential is not a DNI.

For remote machines, `tools/soul_dni_sia_api.py` adds an online delivery
surface without moving the authority boundary into Core. An administrator
creates a short-lived, one-use enrollment token locally; only its SHA-256 is
stored. The device creates its own Ed25519 key, proves possession, and receives
the signed public documents. Renewals require that same device key, a fresh
timestamp, a unique nonce, the current credential sequence, and the immutable
machine identity. Replays, stale writers, key substitution, or a different
machine fail closed. The service defaults to loopback; remote publication must
be behind authenticated HTTPS or a private network boundary.

Operational example on the authority host (the private key remains external):

```bash
python tools/soul_dni_sia_api.py \
  --database /var/lib/soul-sia/registry.sqlite3 \
  --private-key /var/lib/soul-sia/root.pem \
  --state-dir /var/lib/soul-sia/state create-token --label laptop-william
```

## Rotation and revocation

SOUL identity is permanent; its short-lived credential is renewable. A runtime
with an expired credential stops rather than using stale authority. Revocation
is distributed through a signed, expiring trust snapshot; a replacement
snapshot must carry a valid SOUL signature. Live Core also rejects credential
or trust sequence rollback, and Platform promotion requires both sequences to
increase. This v1 deliberately does not claim
immediate revocation while a device is offline. It also does not yet claim
hardware-backed monotonic rollback resistance between two still-live signed
snapshots; that requires an OS secure store/TPM witness.
