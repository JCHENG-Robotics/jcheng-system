# Host systemd units

Base unit files live here, captured from `/etc/systemd/system/` on the robot.

```bash
../capture.sh            # fills this directory from the robot
```

These are the **base** units only. `jcheng-ops` installs drop-ins on top of them
(`Restart=`, `RestartSec=`, `OOMScoreAdjust=`, runtime watchdog). Those are
owned by `jcheng-ops/robot/install-pi-ops.sh` and are not copied here; inspecting
`systemctl cat <unit>` on the robot shows the merged result.

Units expected in this directory:

| Unit | Started by | Notes |
|---|---|---|
| `start_node.service` | boot, enabled | vendor `bringup.launch.py`, foreground; starting it moves the arm |
| `armpi-rs-depth.service` | manual/boot, enabled | D435i colour + aligned depth; has IR warm-up retry |
| `armpi-vision.service` | boot, enabled | Type=oneshot + RemainAfterExit; vendor `vision.launch.py` |
| `armpi-gateway.service` | boot, enabled | 8765 read-only gateway |
| `armpi-gateway-pose.service` | manual | 8766 pose gateway; arm motion possible |
| `armpi-ext-cam.service` | disabled | retired UVC path; kept for reference |

`jcheng-perception-*.service/.target` are **not** here: they are versioned in
`jcheng-perception/deploy/systemd/`.
