"""Humble import check for the hardware gateway entry point."""

from __future__ import annotations

import pytest


def test_gateway_node_module_imports() -> None:
    # The vendor message packages live on the robot image, not in the CI
    # ros:humble-ros-base container; the import check only runs where they exist.
    pytest.importorskip("ros_robot_controller_msgs")
    pytest.importorskip("servo_controller_msgs")

    from jcheng_vendor import gateway_node

    assert callable(gateway_node.main)
    assert gateway_node.VendorGatewayNode.__name__ == "VendorGatewayNode"
