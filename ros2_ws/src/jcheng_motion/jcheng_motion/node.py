"""M3 mock diagnostic entry point.

The ROS service/action transport adapters are added in M4; this CLI exercises
the same core objects used by the mock gateway and scenarios.
"""

from __future__ import annotations

import json

from jcheng_system_core.config import find_repo_root
from jcheng_system_core.stack import MockStack


def main() -> int:
    stack = MockStack.build(find_repo_root())
    stack.sync_state()
    print(json.dumps({
        "active": stack.arbiter._active is not None,
        "note": "use jcheng_gateway for HTTP execution or jcheng_scenarios for acceptance",
    }, indent=2))
    return 0
