# Fixed-cell PiPER X deployment profile (P2, read-only)

Status: **P2 in progress**. This profile brings up the two USB-CAN channels and
nothing else. It grants no motion authority, sends no CAN frame and does not
join DDS. The read-only backend is
[`jcheng_vendor.piper_readonly`](../../ros2_ws/src/jcheng_vendor/jcheng_vendor/piper_readonly.py).

Platform record: `jcheng-design` checkout, `docs/ecosystem/17-platform-pivot.md`
and ADR-0007 (fixed cell, PiPER X production execution arm, leader read-only).

## 1. Host

| Item | Value |
|---|---|
| Host | AGX Orin, `192.168.10.101` |
| OS | Ubuntu 24.04.5 LTS, kernel `6.8.12-1029-tegra` |
| Image | JetPack 7.2.2 (L4T 39.2.2), flashed to NVMe on 2026-11-28 |
| CAN driver | in-kernel `gs_usb`; no userspace pyusb probe needed |
| ROS | `/opt/ros/jazzy` (host toolchain, not used by this profile) |

## 2. Channels

Both adapters are bytewerk candleLight `1d50:606f`. The udev rule pins the
interface name to the adapter serial, so enumeration order does not matter.

| Arm | Role | Adapter serial | USB path | Interface |
|---|---|---|---|---|
| Right | leader, teach input only | `002900324148570B20343133` | `1-4.4.3` | `can_leader1` |
| Left | follower, motion output | `0042001F4648571220363032` | `1-4.4.4` | `can_follower1` |

Owner confirmation (2026-11-28): the right arm is the teach arm, the left arm
is the follower. Hand-move test: moving the right arm changed only
`can_leader1` joints; `can_follower1` stayed byte-identical.

Observed firmware state at rest on 2026-11-28:

| Channel | `ctrl_mode` (0x2A1 byte 0) | Motion |
|---|---|---|
| `can_leader1` | `0x02` teaching | free to hand-guide |
| `can_follower1` | `0x01` CAN control, `MOVE J` | holds position |

## 3. Install

Copy the two files and enable the oneshot unit:

```bash
sudo install -m 0644 99-piper-can.rules /etc/udev/rules.d/99-piper-can.rules
sudo install -m 0644 piper-can-up.service /etc/systemd/system/piper-can-up.service
sudo udevadm control --reload-rules
sudo systemctl daemon-reload
sudo systemctl enable --now piper-can-up.service
```

If the adapters were already enumerated under the kernel names, re-trigger the
rename (`udevadm trigger --subsystem-match=net --action=add`) or re-plug them.
Do not reload `gs_usb` while another process holds the interfaces.

Verify:

```bash
ip -details -s link show can_leader1
ip -details -s link show can_follower1
```

Expected: `state ERROR-ACTIVE`, `bitrate 1000000`, `sample-point 0.750`.

## 4. Read-only invariants

- No enable, no torque, no motion command, no parameter write is sent by this
  profile or by `piper_readonly`.
- The dynamic name is pinned by serial, so a swapped cable cannot silently
  turn the leader into the commanded arm.
- The follower is the only arm a later motion backend may command. The leader
  must never receive a command frame; `piper_readonly` treats both channels as
  input.
- Passive reading does not move either arm. `tx_packets` on both interfaces
  stays `0`; see the evidence report.

## 5. Feedback frames

Vendor protocol v2, big-endian (matches `piper_sdk` 0.6.2):

| CAN ID | Content |
|---|---|
| `0x2A1` | status: ctrl_mode, arm_status, mode_feed, teach_status, motion_status, trajectory_num, err_code u16 |
| `0x2A5` / `0x2A6` / `0x2A7` | joint pairs 1-2 / 3-4 / 5-6, two int32, unit 0.001 deg |
| `0x2A8` | gripper angle int32 (0.001 deg), effort int16, status u8 |

The follower channel additionally emits `0x1C0`-`0x1C3` all-zero frames at
about 5 Hz. They originate from the arm itself, not from the host.

## 6. Calibration caveat

The vendor feedback zero is **not** the URDF zero. On 2026-11-28 the leader
read `j2=-1.891 deg`, `j3=2.516 deg` while the simulation profile expects
`j2 0..180 deg` and `j3 -170..0 deg`. Do not enforce URDF joint limits on
feedback until a per-unit calibration record exists; the simulation profile
stays simulation-only. This is risk R38 in the pivot record.

## 7. Rollback

```bash
sudo systemctl disable --now piper-can-up.service
sudo ip link set can_leader1 down
sudo ip link set can_follower1 down
```

Rollback stops the host-side readers only. It does not change the arm firmware
state and does not touch any vendor container.
