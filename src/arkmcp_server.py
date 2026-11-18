"""ArkDevkitMcp entry point.

Claude Desktop starts this with the DevKit's bundled Python:
    E:\\ARKDevkit\\Engine\\Binaries\\ThirdParty\\Python3\\Win64\\python.exe -u C:\\Dev\\Tools\\ArkDevkitMcp\\src\\arkmcp_server.py

    --check   connect to the open editor once, print the status and exit (no Claude needed)
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from arkmcp import SERVER_NAME, VERSION, config  # noqa: E402
from arkmcp.mcp import McpServer  # noqa: E402
from arkmcp.tools import INSTRUCTIONS, TOOLS, _status, make_handler  # noqa: E402
from arkmcp.ue_link import LinkError, UnrealLink  # noqa: E402


def log(msg):
    # stdout belongs to the MCP protocol; everything else goes to stderr (Claude Desktop's server log).
    sys.stderr.write(f"[arkmcp] {msg}\n")
    sys.stderr.flush()


def main(argv):
    settings, notes = config.load()
    for n in notes:
        log(n)
    link = UnrealLink(settings, log)
    try:
        if "--check" in argv:
            try:
                with link.session():
                    status = _status(link, settings)
                print(json.dumps(status, indent=1, default=str))
                return 1 if "problem" in status else 0
            except LinkError as e:
                print(f"FAILED: {e}")
                return 1
        log(f"ArkDevkitMcp {VERSION} ready (multicast {settings['multicast_group_endpoint']}, "
            f"bind {settings['multicast_bind_address']})")
        McpServer(SERVER_NAME, VERSION, TOOLS, make_handler(link, settings, log), log, INSTRUCTIONS).serve()
        return 0
    finally:
        link.shutdown()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
