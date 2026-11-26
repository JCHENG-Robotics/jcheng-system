# Hardware-backed HTTP gateway composition — evidence

> Date: 2026-11-26. Level: **L2 offline** (fake bridge; no ROS, no device, no
> hardware). This is the composition step for the owner's plan B: the M4/M5
> target is `jcheng_system_core` + the M5.2 `jcheng_vendor` executor.

## 1. What changed

- `jcheng_vendor.gateway_stack.VendorStack` wires the M5.2 `HardwareExecutor`
  into the HTTP `GatewayApp`.
- `VendorGatewayHardware` adapts the bridge to the read surface:
  `state(now)`, `bus_read(now)`, `hold(now)`, `bus_failed`.
- `MockStack` carries `mode="mock"` / `capabilities=("mock",)`;
  `VendorStack` carries `mode="hardware"` / `capabilities=("hardware",)`.
- `GatewayApp` `/v1/health` and the WS hello read those values instead of the
  hard-coded `mock`.

## 2. Tests

`tests/test_vendor_gateway_stack.py`:

| Case | Result |
|---|---|
| vendor health | `capabilities: ["hardware"]`; WS `mode: hardware` |
| mock health | `capabilities: ["mock"]`; WS `mode: mock` |
| `/v1/motion/execute` over the vendor stack | 200, `COMPLETED`, `bus_verified=true`, `send_step` reached the bridge |

The executor-side C2p ordering is covered by
[`../20261126_vendor_executor_c2p/`](../20261126_vendor_executor_c2p/README.md):
send and stop share `_active_lock`.

## 3. Verification

```text
python3 -m pytest -q       pass (no failures)
scripts/check_repo_gate.sh OK
```

Raw output: [`pytest.txt`](pytest.txt), [`gate.txt`](gate.txt).

## 4. Not yet done

- A ROS process that owns `VendorBridge`, polls the bus at about 0.25 s, starts
  `GatewayServer`, and runs `arbiter.tick`. `VendorStack.sync_state` uses the
  last cached bridge state; it is not a bus poll.
- A launch entry and token/port configuration.
- A hardware C1–C5 session on the chosen gateway.
- The application-side M4 enablement (non-mock gateway, L5 evidence, explicit
  per-session authorization).

No hardware was opened; no command was published. The fake bridge is not a
hardware, bus or physical-stop claim.
