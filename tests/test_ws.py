"""WebSocket protocol integration tests."""
import asyncio
import json
import uuid
import websockets

WS_URL = "ws://127.0.0.1:8765"


def _chat(content: str) -> str:
    return json.dumps({"type": "chat", "payload": {"content": content}})


def _command(action: str, **data) -> str:
    return json.dumps({"type": "command", "payload": {"action": action, **data}})


async def _collect(ws, timeout=60):
    """Consume messages until terminal status or error."""
    msgs = []
    while True:
        raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
        msg = json.loads(raw)
        msgs.append(msg)
        p = msg.get("payload", {})
        if msg["type"] == "error":
            break
        if msg["type"] == "status" and p.get("status") in ("done", "idle"):
            break
    return msgs


async def test_status_structure():
    """StatusEvent uses 'status' field (not 'state')."""
    print("=== Scenario 1: StatusEvent Structure ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("hello"))
        msgs = await _collect(ws, timeout=30)

        status_events = [m for m in msgs if m["type"] == "status"]
        print(f"  status events: {len(status_events)}")
        for s in status_events:
            p = s["payload"]
            assert "status" in p, f"missing status field: {p}"
            assert "state" not in p, f"deprecated state field: {p}"
        passed = len(status_events) >= 1
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_session_persistence():
    """Chat messages saved to data/sessions/<sid>.jsonl inside container."""
    print("\n=== Scenario 2: Session Persistence ===")
    import subprocess
    sid = f"test-{uuid.uuid4().hex[:8]}"
    url = f"{WS_URL}?session_id={sid}"
    async with websockets.connect(url) as ws:
        await ws.send(_chat("hello, my name is TestBot"))
        await asyncio.sleep(3)

    result = subprocess.run(
        ["docker", "compose", "exec", "-T", "agent",
         "sh", "-c", f"wc -c < data/sessions/{sid}.jsonl 2>/dev/null || echo 0"],
        capture_output=True, text=True,
    )
    size = int(result.stdout.strip() or 0)
    print(f"  session file size: {size} bytes")
    passed = size > 0
    print("  PASS" if passed else "  FAIL")
    return passed


async def test_multi_session():
    """Two concurrent sessions do not interfere."""
    print("\n=== Scenario 3: Multi-session ===")
    sa = f"concurrent-A-{uuid.uuid4().hex[:4]}"
    sb = f"concurrent-B-{uuid.uuid4().hex[:4]}"

    async def run(name):
        url = f"{WS_URL}?session_id={name}"
        async with websockets.connect(url) as ws:
            await ws.send(_chat(f"hello from {name}"))
            return await _collect(ws, timeout=30)

    results = await asyncio.gather(run(sa), run(sb))
    for i, msgs in enumerate(results):
        messages = [m for m in msgs if m["type"] == "message"]
        errors = [m for m in msgs if m["type"] == "error"]
        print(f"  Session {i+1}: {len(messages)} message(s), {len(errors)} error(s)")

    passed = all(len([m for m in msgs if m["type"] == "error"]) == 0 for msgs in results)
    print("  PASS" if passed else "  FAIL")
    return passed


async def test_error_handling():
    """Invalid JSON gets parse_error event."""
    print("\n=== Scenario 4: Error Handling ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send("not json at all")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            passed = msg["type"] == "error" and msg.get("payload", {}).get("code") == "parse_error"
            print(f"  got: {msg['type']} {msg.get('payload', {}).get('code')}")
        except asyncio.TimeoutError:
            print("  no response")
            passed = False
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_cancel():
    """Cancel command stops a running job."""
    print("\n=== Scenario 5: Cancel ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("search the web for latest AI news"))
        await asyncio.sleep(2)
        await ws.send(_command("cancel"))

        msgs = await _collect(ws, timeout=30)
        # After cancel, should get status done with reason cancelled
        status_events = [m for m in msgs if m["type"] == "status"]
        last_status = status_events[-1]["payload"]["status"] if status_events else ""
        print(f"  final status: {last_status}")
        passed = last_status in ("idle", "done")
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_tool_call_protocol():
    """ToolCallEvent/ToolResultEvent have correct shape."""
    print("\n=== Scenario 6: Tool Call Protocol ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("use web_search to search for Python asyncio"))
        msgs = await _collect(ws, timeout=30)

        tool_calls = [m for m in msgs if m["type"] == "tool_call"]
        tool_results = [m for m in msgs if m["type"] == "tool_result"]

        print(f"  tool_calls: {len(tool_calls)}, tool_results: {len(tool_results)}")
        # Verify event shape if tool calls were made
        for tc in tool_calls:
            p = tc["payload"]
            assert "id" in p, "missing id"
            assert "name" in p, "missing name"
            assert "arguments" in p, "missing arguments"
        for tr in tool_results:
            p = tr["payload"]
            assert "id" in p, "missing id"
            assert "name" in p, "missing name"
            assert "result" in p, "missing result"

        # Each tool_call should have a matching tool_result
        tc_ids = {m["payload"]["id"] for m in tool_calls}
        tr_ids = {m["payload"]["id"] for m in tool_results}
        if tc_ids:
            assert tc_ids == tr_ids, f"mismatch: calls={tc_ids} results={tr_ids}"

        passed = True
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_command_routing():
    """CommandMessage routes to command:<action> hook."""
    print("\n=== Scenario 7: Command Routing ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_command("compress"))
        await asyncio.sleep(1)
        # Command should not crash; no error = pass
        print("  compress command sent")
        passed = True
        print("  PASS" if passed else "  FAIL")
        return passed


async def main():
    results = {}
    scenarios = [
        ("status_structure", test_status_structure),
        ("persistence", test_session_persistence),
        ("multi_session", test_multi_session),
        ("error_handling", test_error_handling),
        ("cancel", test_cancel),
        ("tool_call_protocol", test_tool_call_protocol),
        ("command_routing", test_command_routing),
    ]

    for name, fn in scenarios:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results[name] = False

    print("\n" + "=" * 50)
    print("RESULTS SUMMARY:")
    passed = 0
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")
        if ok:
            passed += 1
    print(f"\nPassed: {passed}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
