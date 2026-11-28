#!/usr/bin/env bash
# Install the fixed-cell PiPER CAN profile. Operator action, never CI.
# Brings up can_leader1/can_follower1 at 1 Mbps; sends no CAN frame.
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
  echo "install.sh must run as root (operator action)" >&2
  exit 1
fi

here=$(cd "$(dirname "$0")" && pwd)

install -m 0644 "$here/99-piper-can.rules" /etc/udev/rules.d/99-piper-can.rules
install -m 0644 "$here/piper-can-up.service" /etc/systemd/system/piper-can-up.service

udevadm control --reload-rules
systemctl daemon-reload
systemctl enable --now piper-can-up.service

# A rename only happens on an add event; re-trigger if the kernel names are
# still can0/can1. This does not affect an already-named interface.
udevadm trigger --subsystem-match=net --action=add || true

echo "Installed. Verify with: ip -details -s link show can_leader1"
