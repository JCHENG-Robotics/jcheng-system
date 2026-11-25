#!/usr/bin/env bash
# Recreate the ArmPi Ultra runtime container with the settings verified on
# 2026-11-25 (see ../README.md §4 and ../docs/ACCEPTANCE-20261125.md).
#
# The container is only a shell: Config.Cmd is `tail -f /dev/null` and every
# workload is started by host systemd units that `docker exec` into it. This
# script does NOT start any workload.
#
# SAFETY: starting the vendor stack (systemctl start start_node) moves the arm.
#
# Usage:
#   ./run.sh [IMAGE]
#
# Environment overrides:
#   ARMPI_CONTAINER_NAME  default ArmPiUltra
#   ARMPI_IMAGE           default: `tag:` from image.lock
#   ARMPI_HOSTDATA        default /home/pi/docker/tmp (mounted at /home/ubuntu/shared)
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
NAME="${ARMPI_CONTAINER_NAME:-ArmPiUltra}"
IMAGE="${1:-${ARMPI_IMAGE:-$(awk '/^tag:[[:space:]]/{print $2}' "$HERE/image.lock")}}"
HOSTDATA="${ARMPI_HOSTDATA:-/home/pi/docker/tmp}"

[ -n "$IMAGE" ] || { echo "ERROR: no image given and image.lock has no tag:" >&2; exit 2; }
docker image inspect "$IMAGE" >/dev/null 2>&1 || {
  echo "ERROR: image $IMAGE not found locally" >&2
  echo "       build it with container/flatten.sh, or pull the registry tag" >&2
  exit 2
}

if docker container inspect "$NAME" >/dev/null 2>&1; then
  echo "ERROR: container $NAME already exists." >&2
  echo "       rename it to keep a rollback point: docker rename $NAME ${NAME}-old" >&2
  echo "       or remove it:                        docker rm -f $NAME" >&2
  exit 1
fi

# Every one of these is a bind mount or socket the vendor stack expects. Missing
# host paths used to be silently created by Docker as empty directories, which
# produces confusing GUI/audio failures instead of a clear error.
for p in /dev /etc/asound.conf /etc/machine-id "$HOSTDATA" \
         /run/user/1000/bus /run/user/1000/pulse /tmp/.X11-unix /var/lib/dbus; do
  [ -e "$p" ] || { echo "ERROR: missing host path $p" >&2; exit 1; }
done
[ -e /dev/snd ] || { echo "ERROR: missing /dev/snd" >&2; exit 1; }

echo "creating $NAME from $IMAGE"
exec docker run -d --name "$NAME" \
  --privileged --network host --hostname raspberrypi \
  --restart always --shm-size 64m \
  --security-opt label=disable \
  --device /dev/snd \
  -e DISPLAY=:0 \
  -e PULSE_SERVER=unix:/run/user/1000/pulse/native \
  -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/1000/bus \
  -e LIBGL_ALWAYS_SOFTWARE=1 \
  -e LANG=C.UTF-8 -e LC_ALL=C.UTF-8 \
  -e XAUTHORITY= \
  -v /dev:/dev \
  -v /etc/asound.conf:/etc/asound.conf \
  -v /etc/machine-id:/etc/machine-id \
  -v "$HOSTDATA":/home/ubuntu/shared \
  -v /run/user/1000/bus:/run/user/1000/bus \
  -v /run/user/1000/pulse:/run/user/1000/pulse \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v /var/lib/dbus:/var/lib/dbus \
  "$IMAGE" tail -f /dev/null
