"""Unit tests for SubJobTool."""
import asyncio


async def test_empty_tasks():
    from agent.tools.subjob import SubJobTool
    tool = SubJobTool()
    result = await tool.execute({"tasks": []})
    assert "no tasks" in result, f"unexpected: {result}"


async def test_no_loop():
    from agent.tools.subjob import SubJobTool
    tool = SubJobTool()
    result = await tool.execute({"tasks": [{"description": "test"}]}, ctx=None)
    assert "unavailable" in result, f"unexpected: {result}"


async def test_result_aggregation():
    from agent.tools.subjob import SubJobTool

    class MockLoop:
        async def spawn(self, desc, ctx):
            return f"Result for: {desc}"

    class MockCtx:
        _loop = MockLoop()

    tool = SubJobTool()
    tasks = [
        {"description": "task-a"},
        {"description": "task-b"},
        {"description": "task-c"},
    ]
    result = await tool.execute({"tasks": tasks}, ctx=MockCtx())
    assert "## Sub-task 1" in result
    assert "Result for: task-a" in result
    assert "Result for: task-b" in result
    assert "Result for: task-c" in result
    assert "---" in result


async def main():
    results = {}
    scenarios = [
        ("empty_tasks", test_empty_tasks),
        ("no_loop", test_no_loop),
        ("result_aggregation", test_result_aggregation),
    ]

    for name, fn in scenarios:
        try:
            await fn()
            results[name] = True
            print(f"  {name}: PASS")
        except Exception as e:
            print(f"  {name}: FAIL - {e}")
            results[name] = False

    passed = sum(1 for v in results.values() if v)
    print(f"\nPassed: {passed}/{len(results)}")
    return passed == len(results)


if __name__ == "__main__":
    asyncio.run(main())
