"""L3 probe: teleop intent -> system intake -> simulation bridge.

Run on domain 43 with the simulation mock plant and the runtime started with
`sim_bridge:=true`. It arms the system, acquires a priority-40 arm lease,
publishes a short teleop velocity script, sends a neutral frame, and prints the
simulated joint positions. Test-only; no hardware.
"""

import time
from pathlib import Path

import rclpy
import yaml
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, HistoryPolicy
from jcheng_interfaces.msg import TeleopCommand
from jcheng_interfaces.srv import AcquireLease, ArmSystem, ReleaseLease
from sensor_msgs.msg import JointState

_LOCK = Path(__file__).resolve().parents[1] / "locks" / "ecosystem.lock.yaml"
CONTRACT = yaml.safe_load(_LOCK.read_text(encoding="utf-8"))["repos"]["jcheng-interfaces"][
    "contract_hash"
]

rclpy.init()
node = Node("teleop_sim_probe")
latest = {"msg": None}
def on_state(msg):
    latest["msg"] = msg
sub_qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
sub = node.create_subscription(JointState, "/sim/joint_states", on_state, sub_qos)
arm_client = node.create_client(ArmSystem, "/jcheng/safety/arm")
lease_client = node.create_client(AcquireLease, "/jcheng/authority/acquire_lease")
for client in (arm_client, lease_client):
    if not client.wait_for_service(timeout_sec=10.0):
        raise SystemExit("service unavailable")
arm = ArmSystem.Request()
arm.operator_id = "probe"
arm.checklist_hash = "probe"
arm.confirm_phrase = "I_HAVE_A_CLEAR_WORKSPACE_AND_SUPPORT"
arm.contract.major = 0
arm.contract.minor = 1
arm.contract.patch = 0
arm.contract.contract_hash = CONTRACT
arm.profile_hash = ""
arm_future = arm_client.call_async(arm)
rclpy.spin_until_future_complete(node, arm_future, timeout_sec=10.0)
arm_resp = arm_future.result()
print("arm", arm_resp.armed, arm_resp.message)
lease = AcquireLease.Request()
lease.holder = "probe"
lease.scope = "arm"
lease.mode = "exclusive"
lease.priority = 40
lease.ttl_ms = 30000
lease.deadman_required = True
lease.contract.major = 0
lease.contract.minor = 1
lease.contract.patch = 0
lease.contract.contract_hash = CONTRACT
lease_future = lease_client.call_async(lease)
rclpy.spin_until_future_complete(node, lease_future, timeout_sec=10.0)
lease_resp = lease_future.result()
print("lease", lease_resp.granted, lease_resp.lease.lease_id, lease_resp.reason_code)
if not lease_resp.granted:
    raise SystemExit("lease rejected")
qos = QoSProfile(depth=1, history=HistoryPolicy.KEEP_LAST, reliability=ReliabilityPolicy.BEST_EFFORT)
pub = node.create_publisher(TeleopCommand, "/jcheng/intent/teleop", qos)
for i in range(40):
    msg = TeleopCommand()
    msg.header.stamp = node.get_clock().now().to_msg()
    msg.seq = i
    msg.lease_id = lease_resp.lease.lease_id
    msg.source = "probe"
    msg.contract.major = 0
    msg.contract.minor = 1
    msg.contract.patch = 0
    msg.contract.contract_hash = CONTRACT
    msg.mode = "JOINT_VELOCITY"
    msg.joint_velocity_rad_s = [0.5, 0.0, 0.0, 0.0, 0.0]
    msg.deadman = i < 36
    pub.publish(msg)
    rclpy.spin_once(node, timeout_sec=0.0)
    time.sleep(0.02)
# neutral frame
msg = TeleopCommand()
msg.header.stamp = node.get_clock().now().to_msg()
msg.seq = 40
msg.lease_id = lease_resp.lease.lease_id
msg.source = "probe"
msg.contract.major = 0
msg.contract.minor = 1
msg.contract.patch = 0
msg.contract.contract_hash = CONTRACT
msg.mode = "JOINT_VELOCITY"
msg.joint_velocity_rad_s = [0.0, 0.0, 0.0, 0.0, 0.0]
msg.deadman = False
pub.publish(msg)
for _ in range(20):
    rclpy.spin_once(node, timeout_sec=0.05)
if latest["msg"] is not None:
    print("sim_positions", list(latest["msg"].position))
else:
    print("sim_positions", "no state")
release_client = node.create_client(ReleaseLease, "/jcheng/authority/release_lease")
if release_client.wait_for_service(timeout_sec=5.0):
    req = ReleaseLease.Request()
    req.lease_id = lease_resp.lease.lease_id
    fut = release_client.call_async(req)
    rclpy.spin_until_future_complete(node, fut, timeout_sec=5.0)
node.destroy_node()
rclpy.shutdown()
