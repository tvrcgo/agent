"""Unit tests for SubJobTool."""
import asyncio


async def test_empty_tasks():
    from agent.tools.subjob import SubJobTool
    tool = SubJobTool()
    result = await tool.execute({"jobs": []})
    assert "no jobs" in result, f"unexpected: {result}"


async def test_no_loop():
    from agent.tools.subjob import SubJobTool
    tool = SubJobTool()
    result = await tool.execute({"jobs": [{"content": "test"}]}, ctx=None)
    assert "unavailable" in result, f"unexpected: {result}"


async def test_result_aggregation():
    from agent.tools.subjob import SubJobTool

    class MockLoop:
        async def spawn(self, content, ctx):
            return f"Result for: {content}"

    class MockCtx:
        _loop = MockLoop()

    tool = SubJobTool()
    jobs = [
        {"content": "job-a"},
        {"content": "job-b"},
        {"content": "job-c"},
    ]
    result = await tool.execute({"jobs": jobs}, ctx=MockCtx())
    assert "## Sub-job 1" in result
    assert "Result for: job-a" in result
    assert "Result for: job-b" in result
    assert "Result for: job-c" in result
    assert "---" in result


async def test_too_many_jobs():
    from agent.tools.subjob import SubJobTool

    class MockLoop:
        async def spawn(self, content, ctx):
            return f"Result for: {content}"

    class MockCtx:
        _loop = MockLoop()

    tool = SubJobTool()
    jobs = [{"content": f"job-{i}"} for i in range(6)]
    result = await tool.execute({"jobs": jobs}, ctx=MockCtx())
    assert "at most 5" in result, f"unexpected: {result}"


async def main():
    results = {}
    scenarios = [
        ("empty_tasks", test_empty_tasks),
        ("no_loop", test_no_loop),
        ("result_aggregation", test_result_aggregation),
        ("too_many_jobs", test_too_many_jobs),
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
