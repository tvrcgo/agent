from __future__ import annotations

"""Unit tests for SubJobTool."""
import asyncio
from dataclasses import dataclass


@dataclass
class MockJob:
    id: str = "test-job"
    session_id: str = "test-session"
    status: str = "pending"


async def test_empty_tasks():
    from agent.tools.subjob import SubJobTool
    from agent.core.loop import AgentContext

    tool = SubJobTool()
    ctx = AgentContext()
    job = MockJob()
    result = await tool.execute({"jobs": []}, ctx=ctx, job=job)
    assert "no jobs" in result, f"unexpected: {result}"


async def test_no_subjob():
    from agent.tools.subjob import SubJobTool
    from agent.core.loop import AgentContext

    tool = SubJobTool()
    ctx = AgentContext()
    job = MockJob()
    result = await tool.execute({"jobs": [{"content": "test"}]}, ctx=ctx, job=job)
    assert "not available" in result, f"unexpected: {result}"


async def test_result_aggregation():
    from agent.tools.subjob import SubJobTool
    from agent.core.loop import AgentContext

    ctx = AgentContext()
    job = MockJob()

    async def mock_subjob(content, parent_job, ctx):
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        future.set_result(f"Result for: {content}")
        return future

    ctx.register("subjob", mock_subjob)

    tool = SubJobTool()
    jobs = [
        {"content": "job-a"},
        {"content": "job-b"},
        {"content": "job-c"},
    ]
    result = await tool.execute({"jobs": jobs}, ctx=ctx, job=job)
    assert "## Sub-job 1" in result
    assert "Result for: job-a" in result
    assert "Result for: job-b" in result
    assert "Result for: job-c" in result
    assert "---" in result


async def test_too_many_jobs():
    from agent.tools.subjob import SubJobTool
    from agent.core.loop import AgentContext

    ctx = AgentContext()
    job = MockJob()

    async def mock_subjob(content, parent_job, ctx):
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        future.set_result(f"Result for: {content}")
        return future

    ctx.register("subjob", mock_subjob)

    tool = SubJobTool()
    jobs = [{"content": f"job-{i}"} for i in range(6)]
    result = await tool.execute({"jobs": jobs}, ctx=ctx, job=job)
    assert "at most 5" in result, f"unexpected: {result}"


async def test_max_depth_reached():
    """Subjob returns error when max depth is reached."""
    from agent.tools.subjob import SubJobTool
    from agent.core.io import InputMessage
    from agent.core.loop import AgentContext, Job

    ctx = AgentContext()
    parent_job = Job(
        id="parent-job",
        status="thinking",
        input=InputMessage(content="test"),
    )

    calls = []
    async def mock_subjob(content, parent_job, ctx):
        calls.append(content)
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        future.set_result(f"Error: maximum sub-job depth (2) reached")
        return future

    ctx.register("subjob", mock_subjob)

    tool = SubJobTool()
    result = await tool.execute({"jobs": [{"content": "task A"}]}, ctx=ctx, job=parent_job)

    assert "maximum sub-job depth" in result
    print(f"  max depth error returned: {result[:60]}...")


async def test_parallel_execution():
    """Multiple subjobs run in parallel and aggregate results."""
    from agent.tools.subjob import SubJobTool
    from agent.core.io import InputMessage
    from agent.core.loop import AgentContext, Job

    ctx = AgentContext()
    parent_job = Job(
        id="parent-job",
        status="thinking",
        input=InputMessage(content="test"),
    )

    results = {}
    async def mock_subjob(content, parent_job, ctx):
        future: asyncio.Future[str] = asyncio.get_event_loop().create_future()
        await asyncio.sleep(0.1)
        results[content] = f"Result for {content}"
        future.set_result(results[content])
        return future

    ctx.register("subjob", lambda content, parent_job, ctx: mock_subjob(content, parent_job, ctx))

    tool = SubJobTool()
    jobs = [
        {"content": "task-A"},
        {"content": "task-B"},
        {"content": "task-C"},
    ]
    result = await tool.execute({"jobs": jobs}, ctx=ctx, job=parent_job)

    assert "Sub-job 1" in result
    assert "Sub-job 2" in result
    assert "Sub-job 3" in result
    assert "---" in result
    print(f"  parallel results aggregated")


async def main():
    results = {}
    scenarios = [
        ("empty_tasks", test_empty_tasks),
        ("no_subjob", test_no_subjob),
        ("result_aggregation", test_result_aggregation),
        ("too_many_jobs", test_too_many_jobs),
        ("max_depth_reached", test_max_depth_reached),
        ("parallel_execution", test_parallel_execution),
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
