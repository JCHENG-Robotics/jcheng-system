# Evidence — 2026-11-25 container rebuild

## Pre-change baseline (09:48 HKT)

Taken immediately before the workload was stopped; the disk numbers already
include the earlier cache cleanup performed the same morning.

| File | Content |
|---|---|
| `container-env.txt` | exact `docker exec ArmPiUltra env \| sort` (10 lines) |
| `nodes-before.txt` | exact `ros2 node list \| sort` (20 lines, including the duplicated `/armpi_debug_gateway` DDS name) |
| `disk-before.txt` | `df` + `docker system df` (33 GB used, image 11.61 GB, container 6.21 GB) |

## Post-change capture (14:17 HKT, after the reboot)

Produced by `../capture.sh 20261125`:

| File | Content |
|---|---|
| `container-config.json` | authoritative `docker inspect ArmPiUltra` (running, image `armpi-ultra:flatten-20261125`) |
| `container-env.txt` | re-captured; **diff against the baseline is empty** |
| `nodes.txt` | re-captured; **diff against `nodes-before.txt` is empty** |
| `topics.txt` | 60 topics, sorted |
| `disk.txt` | `df` + `docker system df` (22 GB used / 29 GB free; image 7.101 GB) |
| `images.txt` | `armpi-ultra:flatten-20261125 7e823327a187 7.1GB` |
| `units.txt` | `systemctl is-active` for the seven units |

The `*-before` names mark the pre-change baseline; the unprefixed names are the
post-change state. Re-run `../capture.sh <date>` at any time; it overwrites the
unprefixed files and writes a new dated directory.
