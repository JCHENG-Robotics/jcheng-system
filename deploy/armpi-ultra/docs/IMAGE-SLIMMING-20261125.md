# Image slimming and container rebuild — 2026-11-25

Change window: 09:48–10:14 HKT. Operator on site; workspace clear (vendor startup
moves the arm). Result: root filesystem 44 GB → 22 GB used, image 11.6 GB → 7.1 GB.

English canonical.

## 1. Summary

| Metric | Before | After |
|---|---|---|
| Root filesystem (`/dev/mmcblk0p2`) | 44 GB used / 7.0 GB free (87%) | **22 GB used / 29 GB free (43%)** |
| Image | `ros:humble` 11.6 GB | `armpi-ultra:flatten-20261125` **7.1 GB** |
| Container writable layer | 6.21 GB | **~1.7 MB** |
| `/var/lib/docker` | 29.9 GB | **7.1 GB** |

The 22 GB reclaimed across the whole session splits roughly into 11 GB of
ordinary cache/log cleanup and 11 GB of Docker layer reclamation.

## 2. Why the image was much larger than its visible content

The container's merged filesystem measured **10.4 GB** while the image plus
writable layer occupied **17.8 GB** on disk. The difference is content that was
deleted *inside* the container:

| Path in the image layer | Size | Visible in container | Evidence |
|---|---|---|---|
| `/home/ubuntu/third_party_ros2/ros1_bridge_ws` | 3.94 GB | no | no ROS 1 runtime (`/opt/ros` has `humble` only), no `roscore`/`ros1_bridge` process, no reference in user code; contains two identical 511 MB `libros1_bridge.so` |
| `/home/ubuntu/noetic_ws` | 913 MB | no | same |
| `/home/ubuntu/third_party_ros2/neovim` | 664 MB | no | editor build, not runtime |

Deleting a file that lives in a lower image layer only writes a whiteout in the
upper layer. `docker commit` keeps the old layers and `docker system prune`
reports 0 B reclaimable; only re-importing the merged filesystem drops the bytes.

## 3. What was removed before flattening (≈3.3 GB)

| Item | Size | Justification |
|---|---|---|
| `/tmp` (154k loose files, cursor tarballs, `rrc_tap.log`, core dumps, test frames) | 1.04 GB | ephemeral; calibration dirs copied to `/home/ubuntu/shared/migration-backup-20261125` first |
| `/root/.vscode-server`, `/root/.cursor-server` | 837 MB | 15 and 4 historical server versions; re-downloaded on next connection |
| `/home/ubuntu/.git` | 632 MB | single-commit snapshot of the whole home; its origin is the vendor's on-prem GitLab, not reachable from the robot network |
| `/home/ubuntu/.ros` | 132 MB | logs from the current week |
| `/home/ubuntu/.nvm` | 196 MB | node version manager; runtime does not use it |
| `/home/ubuntu/.codeium` | 118 MB | old language-server binaries |
| `/home/ubuntu/librealsense-2.55.1` | 90 MB | source tree; driver already installed |
| `/home/ubuntu/third_party_ros2` (remainder) | 90 MB | `armpi-vision-start.sh` documents that this overlay has been unusable since its `build/` was deleted |
| `/home/ubuntu/jcheng_aurora_build` | 47 MB | build dir; `jcheng_aurora_install` kept |
| `ros2_ws_vendor_20261116_141520/.../FastSAM-x.pt` | 145 MB | requires torch, removed in the same session |

## 4. What must stay

| Item | Size | Why |
|---|---|---|
| `/home/ubuntu/ros2_ws` (+`install/`) | 93 MB | every unit sources `/home/ubuntu/ros2_ws/install/setup.bash`; `kinematics`, `ros_robot_controller`, `servo_manager`, `controller_manager` live here |
| `/home/ubuntu/jcheng_aurora_install` | 34 MB | isolated prefix for `deptrum-ros-driver-aurora930` |
| `/home/ubuntu/.local` | 1.3 GB | runtime pip packages (openvino, open3d, mediapipe, pandas, scikit-learn, …) |
| `/home/ubuntu/cyclonedds.xml` (+ backups) | 4 KB | `RMW_IMPLEMENTATION=rmw_cyclonedds_cpp`, `CYCLONEDDS_URI` |
| `/home/ubuntu/jcheng-perception`, `jcheng-m4/m5`, `jcheng-moveit`, `jcheng_hw`, `rs_ws`, `arm_interfaces_ws` | ~180 MB | application code and workspaces |
| `/home/ubuntu/ros2_ws_vendor_20261116_141520` (src only) | 766 MB | de-vendoring baseline |
| `libllvm15` (106 MB) and mesa | — | `LIBGL_ALWAYS_SOFTWARE=1` → llvmpipe uses LLVM |
| `/opt/ros/humble`, `/usr`, `/etc`, `/var` | 4.8 GB | runtime |

## 5. Deferred: dpkg-level strip (~1.3 GB)

Not executed. Candidates and their reverse-dependency evidence:

| Candidate | Size | Reverse dependencies (installed) |
|---|---|---|
| `openjdk-11-jre-headless` + `openjdk-11-jdk-headless` | 243 MB | only other openjdk packages |
| `libvtk9.1`, `python3-vtk9`, `libgdal-dev` | ~200 MB | only `libvtk9-dev` / `vtk9` |
| dev headers (`libboost1.74-dev` 135 MB, `libicu-dev` 45 MB, `libopencv-*-dev`, `/usr/include` 387 MB) | ~400 MB | build-time only |
| toolchain (`gcc-11`, `g++-11`, `gfortran-11`, `cmake`, `clang-format`, `libclang*`, `libllvm14`) | ~230 MB | build-time only; `libllvm15` must stay |
| `/usr/share/doc`, `man`, unused locales | ~250 MB | documentation |

Trade-off: the container loses in-place `colcon build` / source builds. If that is
acceptable, the strip needs a second export/import (another 10–15 min window).

## 6. Data preserved on the robot

| Path | Content | Size |
|---|---|---|
| `/home/pi/docker/migration-20261125/` | old container/image `inspect` JSON, 8 systemd units, env baseline, node/topic baseline, import log, `do-import.sh` | < 1 MB |
| `/home/pi/docker/tmp/migration-backup-20261125/` | `chessboard_*` calibration sets, `calibrationdata.tar.gz`, `ros2_ws_slim.tar.gz`, `joint_limits*.json`, `/tmp` helper scripts | 123 MB |

Both are on the same SD card, so they do not create free space by themselves;
they only protect against accidental loss. They can be removed once the change is
accepted and the calibration data has a permanent home.

## 7. Rejected approaches

| Approach | Why not |
|---|---|
| `docker commit` the cleaned container | keeps the 11.6 GB lower layer; 0 B reclaimed |
| `docker system prune` | `RECLAIMABLE` was 0 B; would only have deleted the running container |
| Rebuild from `ros:humble-ros-base` + a fresh Dockerfile | 323 `ros-humble-*` packages plus openvino/open3d/realsense pins would have to be reproduced; unattended risk |
| Remove `libllvm15` | mesa llvmpipe is the only GL path (`LIBGL_ALWAYS_SOFTWARE=1`) |
| Delete `/home/ubuntu/ros2_ws` | all units source it; motion chain breaks |

## 8. See also

- `../README.md` — run specification, change procedure, rollback, known issues
- `ACCEPTANCE-20261125.md` — verification checklist and residuals
- `JCHENG-Robotics/jcheng-perception` → `docs/FAILURE_MODES.md` §4.1 — D435i depth recovery procedure
