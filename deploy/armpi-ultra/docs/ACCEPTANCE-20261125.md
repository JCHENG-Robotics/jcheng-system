# Acceptance — container rebuild and image flatten, 2026-11-25

Change: `ros:humble` (11.6 GB) → `armpi-ultra:flatten-20261125` (7.1 GB), container
recreated with the spec in `../container/run.sh`. Window 09:48–10:14 HKT.

English canonical.

## 1. Baseline

Taken immediately before the workload was stopped (see `../evidence/20261125/`):

- 20 ROS node names (`sort` order, including the duplicated `/armpi_debug_gateway`)
- 60 topics (count recorded; sorted list pending — `../capture.sh`)
- container env (10 lines) and disk state
- all units active, `systemctl --failed` empty

## 2. Result

| Check | Method | Result |
|---|---|---|
| Container identity | `docker inspect ArmPiUltra` | name `ArmPiUltra`, image `armpi-ultra:flatten-20261125`, `restart=always`, `running=true` |
| Run settings | inspect vs spec | `privileged=true`, `network=host`, `hostname=raspberrypi`, `shm=64 MiB`, `device=/dev/snd`, `security-opt label=disable` |
| Mounts | `/proc/self/mountinfo` in container | all 8 expected mounts present; stale `docker_ros2` bind absent |
| Environment | `docker exec ArmPiUltra env \| sort` vs baseline | **byte-identical** |
| ROS nodes | `ros2 node list \| sort` vs baseline | **diff empty** (20 lines) |
| Topics | `ros2 topic list` | 60, same count as baseline (list capture pending) |
| Colour stream | `ros2 topic hz /camera/color/image_raw` | ~6.5 Hz |
| Aligned depth | `ros2 topic hz /camera/aligned_depth_to_color/image_raw` | ~3.9 Hz |
| Gateways | `ss -ltn`, unauthenticated `curl /` | 8765 and 8766 listening, both return 401 |
| Units | `systemctl is-active` | `start_node`, `armpi-vision`, `armpi-rs-depth`, `armpi-gateway`, `armpi-gateway-pose`, `jcheng-perception.target`, `sync_typerc` all `active` |
| Failed units | `systemctl --failed` | none |
| Restart counts | `systemctl show -p NRestarts` | 0 for all units except the d435i ingress (1, see §3) |
| Stability window | 2 minutes of 20 s samples | d435i ingress present every sample, 0 stalls, no further restarts |
| Disk | `df -h /`, `docker system df` | 22 GB used / 29 GB free (43%); image 7.101 GB; container rw ~1.7 MB |

## 3. Reboot persistence (2026-11-25 14:16 HKT)

The host rebooted after the change. The container came back from
`armpi-ultra:flatten-20261125` under `restart=always` without intervention:

- all seven units `active`, `systemctl --failed` empty
- environment still **byte-identical** to the baseline
- node list converged to the baseline 20 lines; `nodes.txt` re-captured at 14:17,
  diff against `nodes-before.txt` empty
- 60 topics re-captured
- aligned depth ~9.3 Hz after warm-up
- the d435i ingress restarted twice during camera warm-up (14:15:33, 14:16:09)
  and then stayed up: `NRestarts=2`, no stall after 14:16:12

This confirms the image, the run spec and the systemd ordering survive a power
cycle. The warm-up restarts are expected behaviour, see `../README.md` §7.

## 4. Deviations and incidents during the change

1. **d435i ingress crash loop (resolved).** After `docker stop`, the first start
   of the depth node failed at the UVC layer (`dmesg: uvcvideo ... Non-zero
   status (-71)`), so the ingress saw `depth=0` and exited every ~6 s until
   systemd hit its start limit (`Result=start-limit-hit`, `NRestarts=10`).
   `systemctl restart armpi-rs-depth` succeeded on the 4th IR warm-up attempt;
   `reset-failed` + `start` brought the ingress back. Observed stable afterwards
   (one automatic restart at 10:11:35 before it settled).
2. **`sync_typerc.service` failed (resolved).** Its unit retried 7 times against
   the stopped container during the window and tripped the start limit.
   `reset-failed` + `start` restored it once the new container was up.
3. **Colour/depth rates.** The launch profile asks for 15 fps; measured ~6.5 Hz
   colour and ~3.9 Hz aligned depth. **Pre-change rates were not recorded**, so
   this is reported as observed, not as a regression. The Pi 5 is CPU-bound with
   both ingress processes and the gateways running.

## 5. Residual risks

- **No image-level rollback.** `ArmPiUltra-old` and `ros:humble` were deleted
  after this checklist passed. Recovery is `container/run.sh` + `image.lock` +
  the data backups in `../docs/IMAGE-SLIMMING-20261125.md` §6.
- **D435i cold start** remains a hardware characteristic. Recovery procedure is
  in `../README.md` §7 and `JCHENG-Robotics/jcheng-perception` `docs/FAILURE_MODES.md` §4.1.
- **Home snapshot lost.** `/home/ubuntu/.git` (the single-commit home snapshot,
  origin unreachable) was deleted by decision; it does not exist elsewhere.
- **Compiler retained.** Deferred dpkg strip means the image still carries
  ~1.3 GB of build-only packages.

## 6. Evidence index

| Item | Location | State |
|---|---|---|
| Container env baseline | `../evidence/20261125/container-env.txt` | exact |
| Node baseline | `../evidence/20261125/nodes-before.txt` | exact |
| Disk baseline | `../evidence/20261125/disk-before.txt` | exact |
| Topic list | `../evidence/20261125/topics.txt` | captured (60) |
| Container inspect | `../evidence/20261125/container-config.json` | captured |
| Unit states / images | `../evidence/20261125/units.txt`, `images.txt` | captured |
| systemd base units | `../systemd/*.service` | captured (6) |
| Import log and script | robot `/home/pi/docker/migration-20261125/` | on robot |
| Full change narration | `IMAGE-SLIMMING-20261125.md` | this directory |

## 7. Sign-off

| Role | Name | Date | Result |
|---|---|---|---|
| Operator | | | |
| Reviewer (two-reviewer model) | | | |
