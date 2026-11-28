# Streaming capability probe — not performed (robot unreachable)

> Status: **Inconclusive**, 2026-11-26. No servo write, no motion, no container
> change. The probe itself is read-only, but the robot was not reachable from
> this host.

## Attempt

```text
ssh -o BatchMode=yes -o ConnectTimeout=5 pi@10.42.0.218 'hostname; uptime'
ssh: connect to host 10.42.0.218 port 22: Connection timed out
```

One attempt, then stopped (the operator's 3-strike rule). No retry loop, no
ping-based conclusion, no container restart.

## What this does not tell us

Nothing about the vendor servo API. The M6 streaming-executor proposal still
needs the read-only probe in
[`docs/proposals/STREAMING-EXECUTOR.md`](../../docs/proposals/STREAMING-EXECUTOR.md) §5:

1. enumerate the vendor servo/stream API surface in `~/ArmPiUltra/`;
2. determine whether a joint vector can be written atomically and at what rate;
3. record the result (a negative result is valid).

## Preconditions for the next attempt

- The robot is reachable on the operator network (`10.42.0.218:22`), and the
  application layer answers, not just ICMP.
- The probe stays read-only: no enable, no servo write, no vendor container
  restart.
- Any subsequent write session needs separate, explicit, per-session operator
  authorization.
