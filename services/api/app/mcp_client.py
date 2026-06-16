from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict

from .tracing import traceable


ROOT = Path(__file__).resolve().parents[3]
MCP_SERVER_PATH = ROOT / "services" / "mcp_server" / "server.py"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


class ComcastMcpClient:
    """Thin MCP client with a direct fallback for local demo resilience."""

    def __init__(self, prefer_stdio: bool = True) -> None:
        self.prefer_stdio = prefer_stdio

    @traceable(run_type="tool", name="mcp_call_tool")
    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if self.prefer_stdio:
            try:
                return await self._call_tool_stdio(name, arguments)
            except Exception as exc:
                fallback = self._call_tool_direct(name, arguments)
                fallback["_transport_warning"] = f"MCP stdio unavailable; used direct fallback: {exc}"
                return fallback
        return self._call_tool_direct(name, arguments)

    async def _call_tool_stdio(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        params = StdioServerParameters(command=sys.executable, args=[str(MCP_SERVER_PATH)])
        async with stdio_client(params) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                result = await session.call_tool(name, arguments)
                if not result.content:
                    return {}
                first = result.content[0]
                text = getattr(first, "text", "{}")
                return json.loads(text)

    def _call_tool_direct(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        from services.mcp_server import tools

        func = getattr(tools, name, None)
        if not callable(func) or name.startswith("_"):
            raise ValueError(f"Unknown MCP tool: {name!r}")
        return func(**arguments)
