#!/usr/bin/env bash
set -euo pipefail

# Docker Desktop can expose nested Windows directories with ACLs that do not
# map cleanly to a Linux container. Mount one immutable tar instead, verify it,
# extract without host ownership, then drop privileges for every runtime test.
payload_archive="/payload-source/payload.tar"
expected_sha256="${SOUL_PAYLOAD_TAR_SHA256:?SOUL_PAYLOAD_TAR_SHA256 es obligatorio}"
observed_sha256="$(sha256sum "$payload_archive" | awk '{print $1}')"
test "$observed_sha256" = "$expected_sha256"
echo "PAYLOAD_TAR_SHA256_OK=$observed_sha256"
tar --no-same-owner --no-same-permissions -xf "$payload_archive" -C /payload
source_archive="/source-archive/source.tar"
expected_source_sha256="${SOUL_SOURCE_TAR_SHA256:?SOUL_SOURCE_TAR_SHA256 es obligatorio}"
observed_source_sha256="$(sha256sum "$source_archive" | awk '{print $1}')"
test "$observed_source_sha256" = "$expected_source_sha256"
echo "SOURCE_TAR_SHA256_OK=$observed_source_sha256"
tar --no-same-owner --no-same-permissions -xf "$source_archive" -C /source
chmod -R ugo+rwX /payload /source
exec runuser -u qa -- /qa/run-e2e.sh
