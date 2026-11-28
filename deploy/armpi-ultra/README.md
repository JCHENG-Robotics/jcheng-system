# ArmPi Ultra — runtime deployment

The substrate the robot boots into: the vendor ROS 2 Humble container, the host
systemd units that execute into it, and the image those units assume.

> Status: **Frozen** — 2026-11-28. The ArmPi Ultra platform is retired from
> active development; the image, units and evidence are retained for the
> read-only demo and rollback. No new motion work is accepted; only the
> read-only gateway is to remain. Last deployment state (2026-11-25): image
> flattened from 11.6 GB to 7.1 GB; host root filesystem 44 GB → 22 GB used;
> verified against the pre-change baseline (20 ROS nodes, 60 topics, both
> gateways, both cameras).
>
> Freeze decision: `jcheng-design/docs/ecosystem/17-platform-pivot.md` and
> ADR-0007 (approved 2026-11-28, separate checkout). Freeze record:
> `jcheng-design/docs/ecosystem/evidence/armpi-freeze-20261128/README.md`.
> Do not restart the vendor container.
>
> English canonical.

## 1. Why this lives in `jcheng-system`

`JCHENG-Robotics/.github` `docs/repo-taxonomy.md` assigns **deployment** to L2
(`jcheng-system`); this directory is the L2 deployment home for the ArmPi Ultra.

Two boundaries decide what does *not* go here:

| Content | Owner | Reason |
|---|---|---|
| `jcheng-perception-*` units, camera/depth failure modes | `jcheng-perception/deploy/systemd`, `docs/FAILURE_MODES.md` | Capability-owned; ADR 0004 owns camera device ownership |
| Health watch, hardware watchdog, gateway/ssh self-healing | `jcheng-ops` | OPS observes HTTP health only; restarting the vendor container is explicitly out of its scope |
| ROS IDL / contract hashes | `jcheng-interfaces` | No contract change in this deployment |

`jcheng-ops` may **link** here from `docs/RUNBOOK.md`; it must not copy or
automate these steps.

## 2. Layout

```
deploy/armpi-ultra/
├── container/
│   ├── run.sh          # authoritative `docker run` (verified 2026-11-25)
│   ├── flatten.sh      # export|import: reclaims whiteout-deleted image layers
│   ├── publish.sh      # push to a registry + capture the manifest digest
│   └── image.lock      # tag, image id, size, provenance
├── systemd/            # base unit files from /etc/systemd/system (captured)
├── evidence/20261125/  # pre/post baselines and acceptance record
├── evidence/20261127-r10/  # live R10 gateway /v1 evidence
├── install-gateway-vendor.sh   # R10 gateway installer (manual start only)
├── jcheng-gateway-vendor-run.sh
├── docs/
│   ├── IMAGE-SLIMMING-20261125.md
│   ├── R10-GATEWAY-20261127.md
│   └── ACCEPTANCE-20261125.md
└── capture.sh          # re-capture units + baselines from the robot
```

## 3. The container is only a shell

`Config.Cmd = ["tail", "-f", "/dev/null"]`; there is no entrypoint and no
in-container supervisor. Every workload is started by a **host** systemd unit
that `docker exec`s into the container:

| Unit | Role |
|---|---|
| `start_node.service` | vendor `bringup.launch.py` (Type=simple, foreground) |
| `armpi-rs-depth.service` | RealSense D435i colour + aligned depth |
| `armpi-vision.service` | vendor `vision.launch.py` + Aurora930 driver (Type=oneshot) |
| `armpi-gateway.service` | read-only gateway on 8765 |
| `armpi-gateway-pose.service` | pose/motion gateway on 8766 (manual start) |
| `jcheng-gateway-vendor.service` | R10 hardware-backed `/v1` gateway on 8766, loopback, **manual start**, motion gated by token and lease; see `docs/R10-GATEWAY-20261127.md` |
| `armpi-ext-cam.service` | retired UVC path, **disabled** |
| `jcheng-system-readonly.service` | `jcheng_telemetry` `/v1` gateway on 8767, loopback only, `writes: 0` |

`jcheng-system-readonly.service` is the R10 read-only surface, not the legacy
`armpi_debug_gateway`. It serves `GET /v1/health`, `/v1/contract`, `/v1/state`,
`/v1/battery`, `/v1/safety`, `/v1/topics`, `/v1/cameras`, `/v1/tf`,
`/v1/events` and the `/v1/ws/state` WebSocket (101); every mutation path
(`/v1/motion*`, `/v1/leases*`, `/v1/safety/*`) is rejected with 403 and
`/v1/health` reports `writes: 0`. It binds `127.0.0.1`, so it is reachable from
the robot only. Reach it from the operator laptop through an SSH tunnel (`ssh -N
-L 8767:127.0.0.1:8767 pi@10.42.0.218`); do not widen the bind to expose it on
the LAN. Install it with `install-readonly-gateway.sh`; the token lives in
`/etc/jcheng-system/readonly.env` (0600) and is never committed.

The unit runs a **prebuilt** workspace inside the container, selected by
`JCHENG_WS` / `JCHENG_ROOT` in that env file (default
`/home/ubuntu/jcheng-head/ros2_ws`, falling back to `/home/ubuntu/jcheng-m4`).
Build it from a checkout at the pinned interfaces tag — the container already
carries `jcheng_interfaces` v0.1.0 and the vendor `ros_robot_controller_msgs`:

```bash
docker cp <checkout>/. ArmPiUltra:/home/ubuntu/jcheng-head/
docker exec -it ArmPiUltra bash -lc '
  source /opt/ros/humble/setup.bash
  cd /home/ubuntu/jcheng-head/ros2_ws
  colcon build --packages-select jcheng_interfaces jcheng_system_core \
      jcheng_hardware jcheng_telemetry'
```

`jcheng_interfaces` source is taken from the v0.1.0 tree already in the
container (`/home/ubuntu/jcheng-m4/jcheng-interfaces`); the gateway refuses to
serve a contract hash that differs from `configs/contract.yaml`.

The legacy `armpi-gateway-pose` and R10 both use 8766. Mask/stop the legacy
unit before starting R10; the installer refuses while it is active.

The legacy `armpi-gateway-pose` and R10 both use 8766. Mask/stop the legacy
unit before starting R10; the installer refuses while it is active.

Consequences:

- **The container name is an interface.** Every script resolves `ArmPiUltra` by
  name (`CONTAINER="${CONTAINER:-ArmPiUltra}"`) and `systemd` units `docker exec`
  into it. A new container must use the same name.
- **Stopping a unit is not the same as stopping the process.** Almost all units
  have an `ExecStop` that `pkill`s inside the container; `start_node` relies on
  the process tree being killed with the `docker exec` client. After stopping
  the stack, verify with `pgrep` before touching the image (see §5).
- **`/home/ubuntu/shared` is a bind mount**, not image content:
  `/home/pi/docker/tmp` → `/home/ubuntu/shared`. Data written there survives
  container replacement.

## 4. Container run specification

`container/run.sh` is the authoritative copy of the verified settings. Do not
hand-edit the running container; change `run.sh`, then recreate.

| Setting | Value | Why |
|---|---|---|
| `--privileged` | true | historical; the vendor stack expects unrestricted device access |
| `--network host` | true | DDS discovery on the robot LAN; gateways bind `10.42.0.218` |
| `--hostname` | `raspberrypi` | scripts and logs assume it |
| `--restart` | `always` | container must survive crashes |
| `--shm-size` | `64m` | vendor default; large enough for the ROS graph |
| `--security-opt label=disable` | true | AppArmor was disabled for the vendor stack |
| `--device /dev/snd` | true | audio path used by the vendor demo stack |
| `DISPLAY=:0`, `PULSE_SERVER`, `DBUS_SESSION_BUS_ADDRESS` | host user 1000 sockets | GUI/audio pass-through via bind mounts |
| `LIBGL_ALWAYS_SOFTWARE=1` | true | no GPU driver; mesa llvmpipe (**requires `libllvm15` — do not remove**) |
| `LANG`/`LC_ALL` | `C.UTF-8` | avoids locale-dependent vendor parsing |
| `XAUTHORITY` | empty | matches the previously verified environment |
| 8 bind mounts | `/dev`, `/etc/asound.conf`, `/etc/machine-id`, host data, bus, pulse, X11, dbus | keep in sync with `run.sh` |

> The **previous** container's `HostConfig.Binds` contained a stale entry
> `/home/pi/docker_ros2/tmp:/home/ubuntu/share/tmp` whose host path does not
> exist and which is absent from `/proc/self/mountinfo`. It was not part of the
> live configuration and is absent from the recreated container (see
> `evidence/20261125/container-config.json`, 8 mounts). Do not reintroduce it.

## 5. Change procedure

**Starting the vendor stack moves the arm. Keep the workspace clear.**

1. **Capture a baseline** (operator laptop, same network as the robot):

   ```bash
   deploy/armpi-ultra/capture.sh
   ```

2. **Stop the workload** (the container stays up):

   ```bash
   sudo systemctl stop jcheng-perception.target armpi-vision armpi-rs-depth \
     armpi-gateway armpi-gateway-pose armpi-ext-cam start_node
   sudo docker exec -u ubuntu -w /home/ubuntu ArmPiUltra /bin/zsh -c '~/.stop_ros.sh' || true
   ```

   Then poll until nothing matching `ros2 launch|bringup.launch|realsense2_camera|perception_edge|armpi_debug_gateway`
   is left. `docker stop` alone is not sufficient: `docker exec` clients can die
   while the in-container process survives.

3. **Free space inside the container** before flattening (`/tmp` is the usual
   offender; copy calibration data out to `/home/ubuntu/shared` first).

4. **Flatten** (container is stopped by the script):

   ```bash
   deploy/armpi-ultra/container/flatten.sh armpi-ultra:flatten-$(date +%Y%m%d)
   ```

5. **Recreate** with the same name and settings:

   ```bash
   deploy/armpi-ultra/container/run.sh armpi-ultra:flatten-$(date +%Y%m%d)
   ```

6. **Start in dependency order** and poll (no fixed sleeps):

   ```bash
   sudo systemctl start start_node
   # poll until: docker exec -u root ArmPiUltra pgrep -f bringup.launch.py
   sudo systemctl start armpi-rs-depth armpi-vision armpi-gateway
   sudo systemctl start jcheng-perception.target
   ```

7. **Verify** with `docs/ACCEPTANCE-20261125.md`. Keep the previous container
   (`docker rename ArmPiUltra ArmPiUltra-old`) and the previous image until the
   acceptance checklist passes.

### 5.1 Publishing the image

The image is **7.1 GB and does not belong in git**. `image.lock` records the
local tag, image id and provenance; `digest:` stays `not-pushed` until it is
uploaded.

```bash
docker login <registry>                                    # once per host
deploy/armpi-ultra/container/publish.sh <registry>/<repo>:<tag> --update-lock
```

Run it on a host that has the image and good upload bandwidth; the robot's Wi-Fi
is the slow path. `--update-lock` writes the `sha256:` manifest digest back into
`image.lock`, which is what a reviewer should check against the registry.

## 6. Rollback

While the old container and image still exist:

```bash
sudo docker rm -f ArmPiUltra
sudo docker rename ArmPiUltra-old ArmPiUltra
sudo docker start ArmPiUltra
sudo systemctl restart start_node armpi-rs-depth armpi-vision armpi-gateway jcheng-perception.target
```

After they are deleted (as of 2026-11-25) there is no image-level rollback.
Recovery is: recreate from `container/run.sh` + `image.lock`, and re-apply the
data backups listed in `docs/IMAGE-SLIMMING-20261125.md` §6.

## 7. Known operational issues

- **Colour frames but `aligned_depth_to_color` missing** after a container
  restart (dmesg shows `uvcvideo ... Non-zero status (-71)`): the D435i depth
  unit failed to start. Run `sudo systemctl restart armpi-rs-depth` (its IR
  warm-up retries), then
  `sudo systemctl reset-failed jcheng-perception-ingress@d435i_ext && sudo systemctl start ...`.
  Same symptom on a cold boot.
- **After a host reboot** the d435i ingress may restart once or twice while the
  depth unit wakes up (observed 2026-11-25 14:15–14:16, `NRestarts=2`); it
  settles on its own. Only if it reaches the systemd start limit should you use
  the `armpi-rs-depth` procedure above.
- **`sync_typerc.service` failed** right after a container replacement: the unit
  retried 7 times against the old container and hit the start limit. Fix:
  `sudo systemctl reset-failed sync_typerc && sudo systemctl start sync_typerc`.
- **`docker export` may include deleted-at-the-upper-layer content?** No: it
  exports the merged view. Content deleted inside the container disappears from
  the new image — that is the point of the flatten.

## 8. Non-goals

- No `apt purge` of the build toolchain: the container keeps `gcc`/`cmake` so
  `colcon build` still works on the robot. A second flatten could remove
  ~1.3 GB, see `docs/IMAGE-SLIMMING-20261125.md` §5.
- No vendor replacement here. `ros2_ws` (kinematics, `ros_robot_controller`,
  `servo_manager`, `controller_manager`) is still the running motion stack.
- No secrets. Gateway tokens stay in `/home/pi/docker/tmp/armpi_gateway_deploy/token`.

## 9. Freeze (2026-11-28)

| Surface | State |
|---|---|
| Platform | `Frozen` |
| New motion work | Not accepted |
| `jcheng-gateway-vendor.service` | Must not be started again; the on-robot stop is a separate authorized action |
| `jcheng-system-readonly.service` | Retained for the read-only demo |
| Image | `armpi-ultra:flatten-20261125` unchanged |
| Evidence | Unchanged; nothing deleted or moved |

The on-robot stop is a per-session operator action, never a CI or unattended
step. The vendor container is never restarted: its startup check moves the arm.
Rollback is limited to starting the read-only unit; the motion gateway stays
stopped.
