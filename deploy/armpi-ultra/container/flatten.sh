#!/usr/bin/env bash
# Flatten image layers + container writable layer into a single new image.
#
# Why this is needed: deleting files inside a container only writes whiteouts in
# the upper layer. The bytes remain in the image layer, and neither
# `docker commit` nor `docker system prune` can reclaim them. `docker export`
# walks the merged filesystem, so the new image contains only what is visible.
#
# Usage:
#   ./flatten.sh <new-tag> [container-name]
#   ./flatten.sh armpi-ultra:flatten-20261125
#
# The container is stopped by this script (pass --keep-running to fail instead).
# Nothing is restarted: run container/run.sh afterwards.
set -euo pipefail

KEEP_RUNNING=0
ARGS=()
for a in "$@"; do
  case "$a" in
    --keep-running) KEEP_RUNNING=1 ;;
    *) ARGS+=("$a") ;;
  esac
done
set -- "${ARGS[@]}"

if [ "$#" -lt 1 ]; then
  echo "usage: flatten.sh <new-tag> [container-name]" >&2
  exit 2
fi
TAG="$1"
NAME="${2:-${ARMPI_CONTAINER_NAME:-ArmPiUltra}}"

docker container inspect "$NAME" >/dev/null 2>&1 || {
  echo "ERROR: container $NAME does not exist" >&2; exit 2
}
if docker image inspect "$TAG" >/dev/null 2>&1; then
  echo "ERROR: image $TAG already exists; pick another tag" >&2; exit 2
fi

RUNNING="$(docker inspect -f '{{.State.Running}}' "$NAME")"
if [ "$RUNNING" = "true" ]; then
  if [ "$KEEP_RUNNING" = "1" ]; then
    echo "ERROR: $NAME is running; stop the workload before flattening" >&2
    exit 1
  fi
  echo "stopping $NAME"
  docker stop "$NAME" >/dev/null
fi

# Free-space guard: the imported image is roughly the merged filesystem size.
# Over-estimate with image + writable layer (whiteouts only make the real size
# smaller), so a passing guard cannot be defeated by rounding.
AVAIL_KB="$(df -Pk /var/lib/docker | awk 'NR==2{print $4}')"
IMG_ID="$(docker inspect -f '{{.Image}}' "$NAME")"
IMG_KB=$(( $(docker image inspect -f '{{.Size}}' "$IMG_ID") / 1024 ))
RW_KB=$(( $(docker inspect -f '{{.Size}}' "$NAME") / 1024 ))
NEED_KB=$(( IMG_KB + RW_KB ))
if [ "$AVAIL_KB" -lt "$NEED_KB" ]; then
  echo "ERROR: ${AVAIL_KB} KiB free under /var/lib/docker, need ~${NEED_KB} KiB" >&2
  echo "       free space first; a half-written import must be removed with docker rmi" >&2
  exit 1
fi

echo "flattening $NAME -> $TAG (free ${AVAIL_KB} KiB, estimated need ${NEED_KB} KiB)"
docker export "$NAME" | docker import -c 'CMD ["tail","-f","/dev/null"]' - "$TAG"

NEW_ID="$(docker image inspect -f '{{.Id}}' "$TAG")"
NEW_SIZE="$(docker image inspect -f '{{.Size}}' "$TAG")"
echo
echo "created $TAG"
echo "  id:   $NEW_ID"
echo "  size: $(( NEW_SIZE / 1024 / 1024 )) MiB"
echo
echo "next:"
echo "  container/run.sh $TAG"
echo "  sudo systemctl start start_node          # arm moves"
echo "  sudo systemctl start armpi-rs-depth armpi-vision armpi-gateway"
echo "  sudo systemctl start jcheng-perception.target"
echo "  verify with docs/ACCEPTANCE-20261125.md before deleting the old image"
