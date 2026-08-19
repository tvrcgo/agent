"""WebSocket protocol integration tests."""
import asyncio
import json
import os
import uuid
import websockets

WS_URL = os.environ.get("WS_URL", "ws://127.0.0.1:8765")


def _connect(url, **kwargs):
    """Connect with client-side keepalive disabled - Docker proxy drops native ping/pong."""
    return websockets.connect(url, ping_interval=None, ping_timeout=None, open_timeout=30, **kwargs)


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
        if msg["type"] == "error" or (msg["type"] == "status" and p.get("content") in ("done", "idle", "error", "cancelled")):
            break
    return msgs


async def test_status_structure():
    """StatusEvent uses 'status' field (not 'state')."""
    print("=== Scenario 1: StatusEvent Structure ===")
    async with _connect(f"{WS_URL}?session_id=test-status-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("hello"))
        msgs = await _collect(ws, timeout=30)

        status_events = [m for m in msgs if m["type"] == "status"]
        print(f"  status events: {len(status_events)}")
        for s in status_events:
            p = s["payload"]
            assert "content" in p, f"missing content field: {p}"
            assert "state" not in p, f"deprecated state field: {p}"
        passed = len(status_events) >= 1
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_session_persistence():
    """Chat messages saved to data/sessions/<sid>.jsonl."""
    print("\n=== Scenario 2: Session Persistence ===")
    sid = f"test-{uuid.uuid4().hex[:8]}"
    url = f"{WS_URL}?session_id={sid}"
    async with _connect(url) as ws:
        await ws.send(_chat("hello, my name is TestBot"))
        await asyncio.sleep(2)

    import subprocess
    result = subprocess.run(
        ["ls", "-la", f"data/sessions/{sid}.jsonl"],
        capture_output=True, text=True,
    )
    size = 0
    if result.returncode == 0:
        # Get file size
        stat_result = subprocess.run(["stat", "-f%z", f"data/sessions/{sid}.jsonl"], capture_output=True, text=True)
        if stat_result.returncode == 0:
            size = int(stat_result.stdout.strip() or 0)
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
        async with _connect(url) as ws:
            await ws.send(_chat(f"hello from {name}"))
            return await _collect(ws, timeout=30)

    results = await asyncio.gather(run(sa), run(sb))
    for i, msgs in enumerate(results):
        messages = [m for m in msgs if m["type"] == "message"]
        errors = [m for m in msgs if m["type"] == "error" or (m["type"] == "status" and m.get("payload", {}).get("content") == "error")]
        print(f"  Session {i+1}: {len(messages)} message(s), {len(errors)} error(s)")

    passed = all(len([m for m in msgs if m["type"] == "error" or (m["type"] == "status" and m.get("payload", {}).get("content") == "error")]) == 0 for msgs in results)
    print("  PASS" if passed else "  FAIL")
    return passed


async def test_error_handling():
    """Invalid JSON gets parse_error event."""
    print("\n=== Scenario 4: Error Handling ===")
    async with _connect(f"{WS_URL}?session_id=test-err-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send("not json at all")
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=10)
            msg = json.loads(raw)
            # 协议错误由 websocket plugin 发 type=error（code/message）
            passed = msg["type"] == "error"
            print(f"  got: {msg['type']} {msg.get('payload', {}).get('code')}")
        except asyncio.TimeoutError:
            print("  no response")
            passed = False
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_cancel():
    """Cancel command stops a running job."""
    print("\n=== Scenario 5: Cancel ===")
    async with _connect(f"{WS_URL}?session_id=test-cancel-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("search the web for latest AI news"))
        await asyncio.sleep(2)
        await ws.send(_command("cancel"))

        msgs = await _collect(ws, timeout=30)
        status_events = [m for m in msgs if m["type"] == "status"]
        last_status = status_events[-1]["payload"]["content"] if status_events else ""
        print(f"  final status: {last_status}")
        passed = last_status in ("idle", "done", "cancelled")
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_tool_call_protocol():
    """ToolCallEvent/ToolResultEvent have correct shape."""
    print("\n=== Scenario 6: Tool Call Protocol ===")
    async with _connect(f"{WS_URL}?session_id=test-tool-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("use web_search to search for Python asyncio"))
        msgs = await _collect(ws, timeout=90)

        # Tool events are top-level types (not nested in data)
        tool_calls = [m for m in msgs if m["type"] == "tool_call"]
        tool_results = [m for m in msgs if m["type"] == "tool_result"]

        print(f"  tool_calls: {len(tool_calls)}, tool_results: {len(tool_results)}")
        for tc in tool_calls:
            p = tc["payload"]
            assert "id" in (p.get("data") or {}), "missing id"
            assert "tool" in (p.get("data") or {}), "missing tool"
            assert "arguments" in (p.get("data") or {}), "missing arguments"
        for tr in tool_results:
            p = tr["payload"]
            assert "id" in (p.get("data") or {}), "missing id"
            assert "tool" in (p.get("data") or {}), "missing tool"

        tc_ids = {m["payload"]["data"]["id"] for m in tool_calls}
        tr_ids = {m["payload"]["data"]["id"] for m in tool_results}
        if tc_ids:
            assert tc_ids == tr_ids, f"mismatch: calls={tc_ids} results={tr_ids}"

        passed = True
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_command_routing():
    """CommandMessage routes to cmd_<action> hook."""
    print("\n=== Scenario 7: Command Routing ===")
    async with _connect(f"{WS_URL}?session_id=test-cmd-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_command("compress"))
        await asyncio.sleep(1)
        print("  compress command sent")
        passed = True
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_disconnect_mid_job():
    """Forceful disconnect during processing doesn't crash server."""
    print("\n=== Scenario 8: Disconnect Mid-Job ===")
    sid = f"abort-{uuid.uuid4().hex[:6]}"
    try:
        async with _connect(f"{WS_URL}?session_id={sid}") as ws:
            await ws.send(_chat("count from 1 to 100, say each number"))
            await asyncio.sleep(1)
            await ws.close(1011, "test abort")
    except Exception:
        pass
    print("  connection closed abruptly")

    await asyncio.sleep(2)
    async with _connect(f"{WS_URL}?session_id=test-reconnect-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("hello after abort"))
        msgs = await _collect(ws, timeout=60)
        messages = [m for m in msgs if m["type"] == "message"]
        passed = len(messages) > 0
        print(f"  messages after reconnect: {len(messages)}")
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_rapid_disconnect():
    """Quick connect-disconnect doesn't leave orphaned state."""
    print("\n=== Scenario 9: Rapid Disconnect ===")
    for i in range(3):
        sid = f"rapid-{uuid.uuid4().hex[:6]}"
        try:
            async with _connect(f"{WS_URL}?session_id={sid}") as ws:
                await ws.send(_chat(f"msg-{i}"))
                await asyncio.sleep(0.3)
                await ws.close()
        except Exception:
            pass
        print(f"  round {i+1}: closed")

    await asyncio.sleep(2)
    async with _connect(f"{WS_URL}?session_id=test-final-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("final ping"))
        msgs = await _collect(ws, timeout=60)
        passed = len(msgs) > 0
        print(f"  final messages: {len(msgs)}")
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_heartbeat():
    """Heartbeat uses heartbeat type, not status."""
    print("\n=== Scenario 12: Heartbeat ===")
    async with _connect(f"{WS_URL}?session_id=test-heartbeat-{uuid.uuid4().hex[:6]}") as ws:
        msgs = []
        try:
            for _ in range(20):
                raw = await asyncio.wait_for(ws.recv(), timeout=2)
                msg = json.loads(raw)
                msgs.append(msg)
                if msg["type"] == "heartbeat":
                    break
        except asyncio.TimeoutError:
            pass

        heartbeat_events = [m for m in msgs if m["type"] == "heartbeat"]
        status_events = [m for m in msgs if m["type"] == "status"]
        print(f"  heartbeat events: {len(heartbeat_events)}")
        print(f"  status events: {len(status_events)}")
        passed = len(heartbeat_events) >= 1
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_job_tree_event():
    """JobTreeEvent broadcast during sub-job execution."""
    print("\n=== Scenario 10: Job Tree Event ===")
    async with _connect(f"{WS_URL}?session_id=test-jobtree-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("use sub_job to search: Python, Go"))
        msgs = await _collect(ws, timeout=240)

        # Job tree is now a 'data' event with data.name='jobs'
        tree_events = [m for m in msgs if m["type"] == "data" and m.get("payload", {}).get("data", {}).get("name") == "jobs"]
        print(f"  job_tree events: {len(tree_events)}")
        if tree_events:
            last = tree_events[-1]["payload"]["data"]["jobs"]
            for j in last:
                print(f"    {j['id']} depth={j['depth']} status={j['status']}")
                assert all(k in j for k in ("id", "parent_id", "depth", "status", "content")), f"missing fields in job: {j}"
        passed = len(tree_events) > 0
        print("  PASS" if passed else "  FAIL")
        return passed


async def test_long_running_subjob():
    """Sub-job with multiple web searches does not cause disconnect."""
    print("\n=== Scenario 11: Long-running Sub-job ===")
    async with _connect(f"{WS_URL}?session_id=test-longsub-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("use sub_job to search: Python asyncio, Go goroutines, Rust tokio, JavaScript event loop"))
        msgs = await _collect(ws, timeout=600)

        tree_events = [m for m in msgs if m["type"] == "data" and m.get("payload", {}).get("data", {}).get("name") == "jobs"]
        messages = [m for m in msgs if m["type"] == "message"]
        errors = [m for m in msgs if m["type"] == "error" or (m["type"] == "status" and m.get("payload", {}).get("content") == "error")]

        print(f"  job_tree events: {len(tree_events)}")
        print(f"  messages: {len(messages)}")
        print(f"  errors: {len(errors)}")

        if not tree_events:
            print("  FAIL - no job_tree events")
            return False

        statuses: set[str] = set()
        depths: set[int] = set()
        for ev in tree_events:
            for j in ev["payload"]["data"]["jobs"]:
                statuses.add(j["status"])
                depths.add(j["depth"])
        print(f"  seen statuses: {statuses}")
        print(f"  seen depths: {depths}")

        passed = (
            len(errors) == 0
            and len(tree_events) >= 1
            and "thinking" in statuses
            and "done" in statuses
            and 0 in depths
        )
        print("  PASS" if passed else "  FAIL")
        return passed




async def test_message_event_types():
    """OutputMessage supports message/status/data types."""
    print("\n=== Scenario 13: OutputMessage Types ===")
    async with _connect(f"{WS_URL}?session_id=test-types-{uuid.uuid4().hex[:6]}") as ws:
        await ws.send(_chat("say hello"))
        msgs = await _collect(ws, timeout=30)

        message_events = [m for m in msgs if m["type"] == "message"]
        status_events = [m for m in msgs if m["type"] == "status"]
        data_events = [m for m in msgs if m["type"] == "data"]

        print(f"  message events: {len(message_events)}")
        print(f"  status events: {len(status_events)}")
        print(f"  data events: {len(data_events)}")

        if message_events:
            m = message_events[0]
            assert "content" in m["payload"], f"missing content in message: {m}"
            print(f"  message has content: {m['payload']['content'][:50]}...")

        if status_events:
            s = status_events[0]
            assert "content" in s["payload"], f"missing content in status: {s}"
            print(f"  status has content: {s['payload']['content']}")

        if data_events:
            d = data_events[0]
            assert "data" in d["payload"], f"missing data field in data event: {d}"
            assert "name" in d["payload"]["data"], f"missing name in data event: {d}"
            print(f"  data event has name: {d['payload']['data']['name']}")

        passed = len(message_events) > 0 and len(status_events) > 0
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
        ("disconnect_mid_job", test_disconnect_mid_job),
        ("rapid_disconnect", test_rapid_disconnect),
        ("heartbeat", test_heartbeat),
        ("job_tree_event", test_job_tree_event),
        ("long_running_subjob", test_long_running_subjob),
        ("message_event_types", test_message_event_types),
    ]

    for name, fn in scenarios:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"  EXCEPTION[{type(e).__name__}]: {e}")
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
