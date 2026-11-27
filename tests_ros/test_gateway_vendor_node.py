"""Humble import check for the hardware gateway entry point."""

from __future__ import annotations


def test_gateway_node_module_imports() -> None:
    from jcheng_vendor import gateway_node

    assert callable(gateway_node.main)
    assert gateway_node.VendorGatewayNode.__name__ == "VendorGatewayNode"
