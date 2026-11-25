#!/usr/bin/env bash
# Capture the authoritative deployment facts from the robot:
#   - base systemd units (/etc/systemd/system/*.service, no drop-ins)
#   - container env, ROS node/topic list, disk and image inventory
#
# Run from the operator laptop while it is on the same network as the robot.
# Drop-ins installed by jcheng-ops (Restart=, OOMScoreAdjust=) are intentionally
# NOT captured here; they belong to the ops repository.
#
# Usage:
#   ./capture.sh [YYYYMMDD]         # default: today
#
# Environment:
#   ARMPI_HOST            default pi@10.42.0.218
#   ARMPI_SSH_KEY         default ~/.ssh/id_ed25519_armpi
#   ARMPI_CONTAINER_NAME  default ArmPiUltra
set -euo pipefail

HOST="${ARMPI_HOST:-pi@10.42.0.218}"
KEY="${ARMPI_SSH_KEY:-$HOME/.ssh/id_ed25519_armpi}"
CONTAINER="${ARMPI_CONTAINER_NAME:-ArmPiUltra}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
STAMP="${1:-$(date +%Y%m%d)}"
OUT="$HERE/evidence/$STAMP"

SSH=(ssh -i "$KEY" -o BatchMode=yes -o IdentitiesOnly=yes -o ConnectTimeout=8 "$HOST")
mkdir -p "$OUT" "$HERE/systemd"

echo "probing $HOST"
"${SSH[@]}" true || { echo "ERROR: cannot reach $HOST from this network" >&2; exit 1; }

echo "systemd base units -> systemd/"
for u in start_node armpi-vision armpi-rs-depth armpi-gateway armpi-gateway-pose armpi-ext-cam; do
  if "${SSH[@]}" "test -f /etc/systemd/system/$u.service"; then
    "${SSH[@]}" "cat /etc/systemd/system/$u.service" > "$HERE/systemd/$u.service"
    echo "  $u.service"
  else
    echo "  WARN: $u.service not present"
  fi
done

echo "runtime facts -> evidence/$STAMP/"
"${SSH[@]}" "timeout 30 sudo docker inspect $CONTAINER" > "$OUT/container-config.json"
"${SSH[@]}" "timeout 20 sudo docker exec $CONTAINER env | sort" > "$OUT/container-env.txt"
"${SSH[@]}" "timeout 40 sudo docker exec -u ubuntu $CONTAINER bash -lc 'source /opt/ros/humble/setup.bash 2>/dev/null; timeout 20 ros2 node list 2>/dev/null | sort'" > "$OUT/nodes.txt"
"${SSH[@]}" "timeout 40 sudo docker exec -u ubuntu $CONTAINER bash -lc 'source /opt/ros/humble/setup.bash 2>/dev/null; timeout 20 ros2 topic list 2>/dev/null | sort'" > "$OUT/topics.txt"
{ "${SSH[@]}" "df -h /"; "${SSH[@]}" "sudo docker system df"; } > "$OUT/disk.txt"
"${SSH[@]}" "sudo docker images --format '{{.Repository}}:{{.Tag}} {{.ID}} {{.Size}}'" > "$OUT/images.txt"
"${SSH[@]}" 'for u in start_node armpi-vision armpi-rs-depth armpi-gateway armpi-gateway-pose jcheng-perception.target sync_typerc; do printf "%-28s %s\n" "$u" "$(systemctl is-active $u 2>/dev/null)"; done' > "$OUT/units.txt"

echo
echo "captured:"
echo "  nodes=$(grep -c . "$OUT/nodes.txt")  topics=$(grep -c . "$OUT/topics.txt")"
echo "  images: $(cat "$OUT/images.txt")"
echo "review the diff against evidence/20261125/ before committing."
