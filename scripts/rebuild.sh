#!/usr/bin/env bash
# Rebuild + restart the compose stack, then drop the images the rebuild orphaned.
#
# Each `docker compose build` retags e.g. openpaper-client:latest onto the new
# image and leaves the previous one dangling (<none>:<none>). Those are NOT
# build cache, so BuildKit's GC never reclaims them — a week of rebuilds had
# piled up 8.2GB of them and filled the Docker VM disk, which made MinIO
# refuse uploads with XMinioStorageFull and broke paper imports.
#
# Usage:
#   scripts/rebuild.sh                # rebuild every service
#   scripts/rebuild.sh server client  # rebuild only these
#   NO_PRUNE=1 scripts/rebuild.sh     # skip the prune step

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

services=("$@")
# macOS ships bash 3.2, where an empty array expands as unset under `set -u`.
# `${arr[@]+"${arr[@]}"}` is the portable "expand to nothing if empty" form.
expand=(${services[@]+"${services[@]}"})

echo "==> Building ${services[*]:-all services}"
docker compose build ${expand[@]+"${expand[@]}"}

echo "==> Starting ${services[*]:-all services}"
# --force-recreate so env_file (server/.env) edits actually reach the
# container; compose otherwise reuses one whose image didn't change.
docker compose up -d --force-recreate ${expand[@]+"${expand[@]}"}

if [[ -n "${NO_PRUNE:-}" ]]; then
    echo "==> Skipping prune (NO_PRUNE set)"
    exit 0
fi

# Dangling images only: never touches tagged images, containers, or volumes.
# This is daemon-wide, so it also clears other projects' orphaned layers.
echo "==> Pruning dangling images"
docker image prune -f

docker system df
