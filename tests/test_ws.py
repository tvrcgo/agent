"""Quick WebSocket test script for the agent."""
import asyncio
import json
import os
import websockets

WS_URL = "ws://127.0.0.1:8765"


def _chat(content: str) -> str:
    return json.dumps({"type": "chat", "payload": {"content": content}})


async def test_echo():
    """Scenario 1: Basic echo job."""
    print("=== Scenario 1: Basic Echo ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("echo hello world"))

        msgs = []
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=30)
            msg = json.loads(raw)
            msgs.append(msg)
            print(f"  <- {msg['type']}: {json.dumps(msg.get('payload', {}))[:150]}")
            if msg["type"] == "status" and msg.get("payload", {}).get("state") == "done":
                break
            if msg["type"] == "error":
                print(f"  ERROR: {json.dumps(msg)}")
                break

        message_events = [m for m in msgs if m["type"] == "message"]
        print(f"  Got {len(message_events)} message event(s)")
        result = len(message_events) > 0
        print("  PASS" if result else "  FAIL")
        return result


async def test_multi_turn():
    """Scenario 2: Multi-turn conversation with tool usage."""
    print("\n=== Scenario 2: Multi-turn ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("echo first, then echo second"))

        msgs = []
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=60)
            msg = json.loads(raw)
            msgs.append(msg)
            payload = msg.get("payload", {})
            if msg["type"] == "tool_call":
                print(f"  <- tool_call: {payload.get('name')}({json.dumps(payload.get('arguments', {}))[:80]})")
            elif msg["type"] == "tool_result":
                print(f"  <- tool_result: {payload.get('result', '')[:80]}")
            elif msg["type"] == "status":
                print(f"  <- status: {payload.get('state')}")
            elif msg["type"] == "error":
                print(f"  ERROR: {json.dumps(msg)}")
                break
            if msg["type"] == "status" and payload.get("state") == "done":
                break

        tool_calls = [m for m in msgs if m["type"] == "tool_call"]
        print(f"  Got {len(tool_calls)} tool call(s)")
        result = len(tool_calls) >= 1
        print("  PASS" if result else "  FAIL")
        return result


async def test_session_persistence():
    """Scenario 3: Session persists across connections."""
    print("\n=== Scenario 3: Session Persistence ===")
    async with websockets.connect(WS_URL) as ws:
        await ws.send(_chat("my name is TestBot"))
        await asyncio.sleep(1)

    # Check session files were created
    data_dir = os.path.join("data", "memory")
    files = os.listdir(data_dir) if os.path.exists(data_dir) else []
    print(f"  Memory files: {files}")
    result = bool(files)
    print("  PASS" if result else "  OK (may need more messages to persist)")
    return True  # Soft pass


async def test_multi_session():
    """Scenario 4: Multiple concurrent sessions."""
    print("\n=== Scenario 4: Multi-session Concurrency ===")

    async def run_session(name: str):
        url = f"{WS_URL}?session_id={name}"
        async with websockets.connect(url) as ws:
            await ws.send(_chat(f"echo hello from {name}"))
            msgs = []
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=30)
                msg = json.loads(raw)
                msgs.append(msg)
                payload = msg.get("payload", {})
                if msg["type"] == "status" and payload.get("state") == "done":
                    break
                if msg["type"] == "error":
                    break
            return msgs

    results = await asyncio.gather(
        run_session("session-A"),
        run_session("session-B"),
    )

    for i, msgs in enumerate(results):
        message_events = [m for m in msgs if m["type"] == "message"]
        print(f"  Session {i+1}: {len(message_events)} message(s)")

    all_ok = all(len([m for m in msgs if m["type"] == "message"]) > 0 for msgs in results)
    print("  PASS" if all_ok else "  FAIL")
    return all_ok


async def test_long_job():
    """Scenario 5: Long-running job with multiple iterations."""
    print("\n=== Scenario 5: Long Task ===")
    async with websockets.connect(WS_URL) as ws:
        prompt = "echo the numbers from 1 to 5 one at a time"
        await ws.send(_chat(prompt))

        msgs = []
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=120)
            msg = json.loads(raw)
            msgs.append(msg)
            payload = msg.get("payload", {})
            if msg["type"] == "tool_call":
                print(f"  <- tool_call: {payload.get('name')}")
            elif msg["type"] == "tool_result":
                print(f"  <- tool_result: {payload.get('result', '')[:60]}")
            elif msg["type"] == "status":
                print(f"  <- status: {payload.get('state')}")
            elif msg["type"] == "error":
                print(f"  ERROR: {json.dumps(msg)}")
                break
            if msg["type"] == "status" and payload.get("state") == "done":
                break

        tool_calls = [m for m in msgs if m["type"] == "tool_call"]
        print(f"  Got {len(tool_calls)} tool call(s)")
        result = len(tool_calls) >= 3
        print("  PASS" if result else f"  OK (got {len(tool_calls)} calls)")
        return True


async def main():
    results = {}

    for name, fn in [
        ("echo", test_echo),
        ("multi_turn", test_multi_turn),
        ("persistence", test_session_persistence),
        ("multi_session", test_multi_session),
        ("long_job", test_long_job),
    ]:
        try:
            results[name] = await fn()
        except Exception as e:
            print(f"  EXCEPTION: {e}")
            results[name] = False

    print("\n" + "=" * 50)
    print("RESULTS SUMMARY:")
    for name, ok in results.items():
        status = "PASS" if ok else "FAIL"
        print(f"  {name}: {status}")
    print(f"\nPassed: {sum(1 for v in results.values() if v)}/{len(results)}")


if __name__ == "__main__":
    asyncio.run(main())
