"""
Unit tests for the describe loop's resilience (batch/build_image_descriptions.py): a transient error (memory, connection)
is never recorded as a permanent failure and stops the run after a few in a row, and whatever was described is committed
however the loop ends. No real DB or Ollama.
"""
import uuid
from types import SimpleNamespace

import pytest

import batch.build_image_descriptions as module
from metrics.listener import SimpleMetricsListener


class _Committer:
    def __init__(self):
        self.saved, self.done, self.closed = [], 0, 0

    def save_description(self, image_id, prompt_key, model, text):
        self.saved.append((image_id, prompt_key, text))

    async def on_image_done(self):
        self.done += 1

    async def close(self):
        self.closed += 1


class _StatusRepo:
    def __init__(self):
        self.failures = []

    async def record_failure(self, image_id, message):
        self.failures.append((image_id, message))


class _Tracker:
    def __init__(self):
        self.done = self.skipped = 0

    def mark_done(self):
        self.done += 1

    def skip(self):
        self.skipped += 1


PROMPT = SimpleNamespace(key="p", prompt="describe")


def _work(n):
    return [(f"{i}.jpg", uuid.uuid4(), [PROMPT]) for i in range(n)]


async def _run(work, describe):
    committer, repo, tracker = _Committer(), _StatusRepo(), _Tracker()
    metrics = SimpleMetricsListener()
    try:
        await module._describe_all(
            work, base_path="base", num_ctx=8192, describer=SimpleNamespace(describe=describe),
            model_for=lambda p: "m", committer=committer, status_repos={"p": repo}, metrics=metrics, tracker=tracker)
        error = None
    except BaseException as e:  # the tests inspect what the loop raised
        error = e
    return committer, repo, tracker, metrics, error


@pytest.mark.parametrize("message", [
    "std::bad_alloc (status code: 400)",
    "model requires more system memory than is available",
    "HTTPConnectionPool: connection refused",
    "timed out",
])
def test_transient_errors_are_recognised(message):
    assert module.is_transient_error(RuntimeError(message))


@pytest.mark.parametrize("message", [
    "prediction aborted, token repeat limit reached (status code: 500)",
    '{"error":{"code":400,"message":"Failed to load image or audio file"}}',
])
def test_genuine_failures_are_not_transient(message):
    assert not module.is_transient_error(RuntimeError(message))


@pytest.mark.asyncio
async def test_transient_error_is_not_recorded_as_a_failure():
    calls = []

    def describe(path, prompt, model, num_ctx):
        calls.append(path)
        if len(calls) == 1:
            raise RuntimeError("std::bad_alloc (status code: 400)")
        return "text"

    committer, repo, tracker, metrics, error = await _run(_work(3), describe)

    assert error is None
    assert repo.failures == []
    assert len(committer.saved) == 2
    assert metrics.counters_dict()["error.transient"] == 1


@pytest.mark.asyncio
async def test_genuine_error_is_recorded_as_a_permanent_failure():
    def describe(path, prompt, model, num_ctx):
        raise RuntimeError("prediction aborted, token repeat limit reached (status code: 500)")

    committer, repo, tracker, metrics, error = await _run(_work(2), describe)

    assert error is None
    assert len(repo.failures) == 2


@pytest.mark.asyncio
async def test_run_stops_after_consecutive_transient_errors_and_still_commits():
    calls = []

    def describe(path, prompt, model, num_ctx):
        calls.append(path)
        raise RuntimeError("connection refused")

    committer, repo, tracker, metrics, error = await _run(_work(20), describe)

    assert isinstance(error, module.DescribeAborted)
    assert module.MAX_CONSECUTIVE_TRANSIENT <= len(calls) < 20
    assert repo.failures == []
    assert committer.closed == 1


@pytest.mark.asyncio
async def test_a_success_resets_the_consecutive_count():
    calls = []

    def describe(path, prompt, model, num_ctx):
        calls.append(path)
        if len(calls) % 3 == 0:
            return "text"
        raise RuntimeError("connection refused")

    committer, repo, tracker, metrics, error = await _run(_work(30), describe)

    assert error is None
    assert len(committer.saved) == 10


@pytest.mark.asyncio
async def test_committer_is_closed_when_the_loop_is_interrupted():
    class Interrupted(BaseException):
        pass

    def describe(path, prompt, model, num_ctx):
        raise Interrupted()

    committer, repo, tracker, metrics, error = await _run(_work(3), describe)

    assert isinstance(error, Interrupted)
    assert committer.closed == 1
