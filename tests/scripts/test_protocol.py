#!/usr/bin/env python
"""Full protocol test for WebSocket message events."""
import asyncio
import json
import uuid
import websockets
import os

WS_URL = os.environ.get("WS_URL", "ws://127.0.0.1:8765")


async def test_tool_call_protocol():
    """Test tool_call and tool_result events are sent correctly."""
    print("=== Test: Tool Call Protocol ===")
    sid = f"test-tool-{uuid.uuid4().hex[:6]}"
    
    async with websockets.connect(
        f"{WS_URL}?session_id={sid}",
        ping_interval=None,
        ping_timeout=None,
        open_timeout=30
    ) as ws:
        # Send a message that will trigger web_search tool
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"content": "search web for Python asyncio"}
        }))
        print(f"Sent: search web for Python asyncio")
        
        events = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=120)
                msg = json.loads(raw)
                events.append(msg)
                
                t = msg.get("type")
                p = msg.get("payload", {})
                print(f"  {t}")
                
                if t == "status" and p.get("content") in ("done", "error", "cancelled"):
                    break
            except asyncio.TimeoutError:
                print("  TIMEOUT")
                break
        
        # Analyze results
        print(f"\nTotal events: {len(events)}")
        
        by_type = {}
        for e in events:
            t = e.get("type")
            by_type[t] = by_type.get(t, 0) + 1
        print(f"By type: {by_type}")
        
        # Check tool events (top-level types)
        tool_calls = []
        tool_results = []
        
        for e in events:
            if e.get("type") == "tool_call":
                d = e.get("payload", {})
                tool_calls.append({**d.get("data", {}), "content": d.get("content", "")})
            elif e.get("type") == "tool_result":
                d = e.get("payload", {})
                tool_results.append({**d.get("data", {}), "content": d.get("content", "")})
        
        print(f"\nTool calls: {len(tool_calls)}")
        for tc in tool_calls:
            print(f"  id={tc.get('id')}, tool={tc.get('tool')}, args={tc.get('arguments')}")
        
        print(f"\nTool results: {len(tool_results)}")
        for tr in tool_results:
            print(f"  id={tr.get('id')}, tool={tr.get('tool')}, error={tr.get('error')}")
        
        # Assertions
        assert len(tool_calls) > 0, "Expected at least one tool_call event"
        assert len(tool_results) > 0, "Expected at least one tool_result event"
        
        # Check matching IDs
        call_ids = {tc.get("id") for tc in tool_calls}
        result_ids = {tr.get("id") for tr in tool_results}
        assert call_ids == result_ids, f"Mismatch: calls={call_ids}, results={result_ids}"
        
        print("\nPASS")
        return True


async def test_message_format():
    """Test message event format."""
    print("\n=== Test: Message Format ===")
    sid = f"test-msg-{uuid.uuid4().hex[:6]}"
    
    async with websockets.connect(
        f"{WS_URL}?session_id={sid}",
        ping_interval=None,
        ping_timeout=None,
        open_timeout=30
    ) as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"content": "say hello"}
        }))
        
        events = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                events.append(msg)
                
                if msg.get("type") == "status" and msg.get("payload", {}).get("content") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                break
        
        # Check message format
        message_events = [e for e in events if e.get("type") == "message"]
        status_events = [e for e in events if e.get("type") == "status"]
        
        assert len(message_events) > 0, "Expected at least one message event"
        assert len(status_events) > 0, "Expected at least one status event"
        
        # Check payload structure
        for e in message_events:
            p = e.get("payload", {})
            assert "content" in p, f"Message missing content: {e}"
        
        for e in status_events:
            p = e.get("payload", {})
            assert "content" in p, f"Status missing content: {e}"
        
        print(f"Messages: {len(message_events)}, Status: {len(status_events)}")
        print("PASS")
        return True


async def test_status_event():
    """Test status event structure."""
    print("\n=== Test: Status Event Structure ===")
    sid = f"test-status-{uuid.uuid4().hex[:6]}"
    
    async with websockets.connect(
        f"{WS_URL}?session_id={sid}",
        ping_interval=None,
        ping_timeout=None,
        open_timeout=30
    ) as ws:
        await ws.send(json.dumps({
            "type": "chat",
            "payload": {"content": "what is 2+2"}
        }))
        
        status_events = []
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=60)
                msg = json.loads(raw)
                
                if msg.get("type") == "status":
                    p = msg.get("payload", {})
                    status_events.append({
                        "content": p.get("content"),
                        "has_data": "data" in p,
                    })
                    print(f"  status: {p.get('content')}")
                
                if msg.get("type") == "status" and msg.get("payload", {}).get("content") in ("done", "error"):
                    break
            except asyncio.TimeoutError:
                break
        
        # Check status values
        status_values = [s["content"] for s in status_events]
        print(f"Status values: {status_values}")
        
        assert "thinking" in status_values or "done" in status_values, f"Unexpected status values: {status_values}"
        
        print("PASS")
        return True


async def main():
    tests = [
        ("message_format", test_message_format),
        ("status_event", test_status_event),
        ("tool_call_protocol", test_tool_call_protocol),
    ]
    
    results = {}
    for name, fn in tests:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"FAIL: {e}")
            results[name] = False
    
    print("\n" + "=" * 50)
    passed = sum(1 for v in results.values() if v)
    print(f"Passed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    asyncio.run(main())
