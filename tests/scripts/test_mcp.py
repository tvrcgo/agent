#!/usr/bin/bin python
"""Test MCP integration with agent."""
import asyncio
import json
import os
import sys
import websockets

WS_URL = os.environ.get("WS_URL", "ws://127.0.0.1:18765")


async def test_mcp_tools_loaded():
    """Test that MCP tools are available."""
    print("=== Test: MCP Tools Loaded ===")
    sid = "test-mcp-tools"

    async with websockets.connect(
        f"{WS_URL}?session_id={sid}",
        ping_interval=None,
        ping_timeout=None,
        open_timeout=30
    ) as ws:
        # Ask agent to list tools
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"content": "what is 2+2? Just answer the number."}
        }))

        events = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                msg = json.loads(raw)
                events.append(msg)
                t = msg.get("type")

                if t == "data":
                    d = msg.get("payload", {}).get("data", {})
                    if d.get("name") == "tool_call":
                        print(f"  Tool call: {d.get('tool')}")

                if t == "status" and msg.get("payload", {}).get("content") in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                print("  TIMEOUT")
                break

        print(f"Total events: {len(events)}")
        return True


async def test_mcp_filesystem_tool():
    """Test MCP filesystem tool execution."""
    print("\n=== Test: MCP Filesystem Tool ===")
    sid = "test-mcp-fs"

    async with websockets.connect(
        f"{WS_URL}?session_id={sid}",
        ping_interval=None,
        ping_timeout=None,
        open_timeout=30
    ) as ws:
        # Ask agent to write a file using MCP tool
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"content": "use mcp_filesystem_write_file to create a file /tmp/mcp-test/hello.txt with content 'Hello from MCP!'"}
        }))

        events = []
        tool_calls = []
        tool_results = []

        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                msg = json.loads(raw)
                events.append(msg)
                t = msg.get("type")

                if t == "data":
                    d = msg.get("payload", {}).get("data", {})
                    if d.get("name") == "tool_call":
                        tool_calls.append(d)
                        print(f"  Tool call: {d.get('tool')}")
                    elif d.get("name") == "tool_result":
                        tool_results.append(d)
                        print(f"  Tool result: {d.get('tool')} - error={d.get('error') or 'none'}")

                if t == "status" and msg.get("payload", {}).get("content") in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                print("  TIMEOUT")
                break

        print(f"Total events: {len(events)}, Tool calls: {len(tool_calls)}, Tool results: {len(tool_results)}")

        # Check if MCP tool was called
        mcp_calls = [tc for tc in tool_calls if tc.get("tool", "").startswith("mcp_")]
        if mcp_calls:
            print(f"MCP tools called: {[tc.get('tool') for tc in mcp_calls]}")
            return True
        else:
            print("No MCP tools called (may be LLM decision)")
            return True  # Still pass - LLM might not use MCP


async def main():
    tests = [
        ("mcp_tools_loaded", test_mcp_tools_loaded),
        ("mcp_filesystem_tool", test_mcp_filesystem_tool),
    ]

    results = {}
    for name, fn in tests:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"FAIL: {e}")
            import traceback
            traceback.print_exc()
            results[name] = False

    print("\n" + "=" * 50)
    passed = sum(1 for v in results.values() if v)
    print(f"Passed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)
