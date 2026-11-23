# 运行手册（M3 Mock）

> M3 只运行 Mock；不连接机器人、不打开串口、不发布厂商话题、不发送真实运动。

## 1. 构建与测试

```bash
source /opt/ros/humble/setup.bash
colcon build --base-paths ros2_ws/src
source install/setup.bash
python3 -m pytest -q
```

## 2. 运行场景

```bash
ros2 run jcheng_testkit jcheng_scenarios --root . \
  --out e2e/reports/m3-mock.json
```

预期：15 passed、2 skipped、required failures 0。

## 3. 运行 Mock 网关

```bash
export JCHENG_GATEWAY_TOKEN="<secret>"
ros2 run jcheng_gateway jcheng_gateway --port 8766
# 或
ros2 launch jcheng_bringup mock_stack.launch.py token:="$JCHENG_GATEWAY_TOKEN"
```

- 默认只绑定 `127.0.0.1`。
- 非 health 端点需要 `Authorization: Bearer <token>`。
- 不连接 ROS 硬件；核心是进程内 Mock。

## 4. 诊断 CLI

```bash
ros2 run jcheng_safety jcheng_safety_check
ros2 run jcheng_authority jcheng_authority_check
ros2 run jcheng_motion jcheng_motion_check
ros2 run jcheng_hardware jcheng_hardware_check
ros2 run jcheng_guard jcheng_guard_check
```

## 5. 锁文件验证

```bash
python3 tools/verify_lock.py --root . \
  --interfaces-root ../jcheng-interfaces \
  --require-evidence
```

`--remote` 会检查 interfaces v0.1.0 标签提交。

## 6. 实机

M3 不包含实机步骤。M4 只读接入前，必须重新评审域、SROS2、容器权限与预检；
任何实机运动仍需单独授权。
