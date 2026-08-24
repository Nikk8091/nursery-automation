"""
tests/test_batch_executor.py — Unit tests for orchestration/batch_executor.py

Owning spec: docs/specs/spec_01_vram_architecture.md §4
Covers: basic batch execution, OOM adaptive halving (0, 1, 2 halvings),
        MANUAL_FLAG escalation, batch_size_used tracking, timeout handling.
"""

import time
from unittest.mock import MagicMock, patch, Mock

import pytest

from db.models import BatchPhase, Generation, GenerationStatus
from engine.comfy_client import ComfyJobResult, ComfyJobState, ComfyOOMError, ComfyUIClient
from orchestration.batch_executor import (
    BatchJob,
    BatchResult,
    run_batch,
    submit_and_poll_single,
    _poll_batch,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_comfy_client():
    """Create a mocked ComfyUIClient."""
    client = Mock(spec=ComfyUIClient)
    client.submit = Mock()
    client.poll_once = Mock()
    client.poll_until_complete = Mock()
    return client


@pytest.fixture
def sample_job():
    """Create a sample BatchJob with a Generation record."""
    gen = Generation(
        generation_id=1,
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
    )
    return BatchJob(
        workflow={"prompt": "test"},
        generation=gen,
    )


@pytest.fixture
def sample_jobs():
    """Create multiple sample BatchJobs."""
    jobs = []
    for i in range(4):
        gen = Generation(
            generation_id=i + 1,
            batch_phase=BatchPhase.C,
            agent_type="visual_prompt_agent",
            model_used="sdxl",
        )
        jobs.append(BatchJob(
            workflow={"prompt": f"test_{i}"},
            generation=gen,
        ))
    return jobs


# ---------------------------------------------------------------------------
# run_batch tests — basic execution
# ---------------------------------------------------------------------------

def test_run_batch_all_complete(mock_comfy_client, sample_jobs):
    """All jobs complete successfully on first attempt."""
    # Setup: submit returns prompt_ids, poll_once returns COMPLETE
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(4)]

    # Poll returns RUNNING once, then COMPLETE
    call_count = 0
    def poll_side_effect(prompt_id):
        nonlocal call_count
        call_count += 1
        if call_count <= 4:
            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.RUNNING,
                output_paths=[],
            )
        return ComfyJobResult(
            prompt_id=prompt_id,
            state=ComfyJobState.COMPLETE,
            output_paths=[f"output_{prompt_id}.png"],
        )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(sample_jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=4)

    assert len(result.completed) == 4
    assert len(result.failed) == 0
    assert len(result.oom_items) == 0
    for job in result.completed:
        assert job.generation.status == GenerationStatus.COMPLETE
        assert job.generation.batch_size_used == 4
        assert job.generation.comfyui_prompt_id is not None


def test_run_batch_mixed_complete_and_failed(mock_comfy_client, sample_jobs):
    """Some jobs complete, some fail with non-OOM error."""
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(4)]

    def poll_side_effect(prompt_id):
        idx = int(prompt_id.split("_")[1])
        if idx % 2 == 0:
            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.COMPLETE,
                output_paths=[f"output_{prompt_id}.png"],
            )
        else:
            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.FAILED,
                output_paths=[],
                error_message="Model not found",
            )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(sample_jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=4)

    assert len(result.completed) == 2
    assert len(result.failed) == 2
    assert len(result.oom_items) == 0
    for job, err in result.failed:
        assert job.generation.status == GenerationStatus.FAILED
        assert "Model not found" in err


# ---------------------------------------------------------------------------
# run_batch tests — OOM adaptive halving
# ---------------------------------------------------------------------------

def test_run_batch_oom_halves_once(mock_comfy_client, sample_jobs):
    """OOM on first batch -> halve batch size -> remaining complete."""
    # 4 jobs, batch_size=4 -> OOM on first 2 -> halve to 2 -> complete
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(6)]  # 4 initial + 2 retry

    # First batch: first 2 OOM, last 2 COMPLETE
    # Retry batch (size 2): both COMPLETE
    poll_states = {
        "prompt_0": [ComfyJobState.OOM],
        "prompt_1": [ComfyJobState.OOM],
        "prompt_2": [ComfyJobState.COMPLETE],
        "prompt_3": [ComfyJobState.COMPLETE],
        "prompt_4": [ComfyJobState.COMPLETE],
        "prompt_5": [ComfyJobState.COMPLETE],
    }

    def poll_side_effect(prompt_id):
        state = poll_states[prompt_id].pop(0)
        if state == ComfyJobState.OOM:
            raise ComfyOOMError("CUDA out of memory")
        return ComfyJobResult(
            prompt_id=prompt_id,
            state=state,
            output_paths=[f"output_{prompt_id}.png"] if state == ComfyJobState.COMPLETE else [],
        )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(sample_jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=4)

    # First 2 jobs: OOM -> retry at batch_size=2 -> COMPLETE
    # Last 2 jobs: COMPLETE on first try
    assert len(result.completed) == 4
    assert len(result.oom_items) == 0
    # Check batch_size_used tracking
    for job in result.completed:
        # First 2 were retried at batch_size=2, last 2 at batch_size=4
        assert job.generation.batch_size_used in (2, 4)


def test_run_batch_oom_halves_twice(mock_comfy_client):
    """OOM -> halve -> OOM again -> halve again -> complete."""
    jobs = []
    for i in range(2):
        gen = Generation(
            generation_id=i + 1,
            batch_phase=BatchPhase.D,
            agent_type="video_gen_agent",
            model_used="wan22",
        )
        jobs.append(BatchJob(workflow={"prompt": f"test_{i}"}, generation=gen))

    # Each submit() call generates a new prompt_id
    # Initial batch (2 jobs): prompt_0, prompt_1
    # Retry batch 1 (size 1): prompt_2 (job0), prompt_3 (job1)
    # Retry batch 2 (size 1): prompt_4 (job0)
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(5)]

    # Job0: prompt_0(OOM) -> prompt_2(OOM) -> prompt_4(COMPLETE)
    # Job1: prompt_1(OOM) -> prompt_3(COMPLETE)
    poll_states = {
        "prompt_0": [ComfyJobState.OOM],
        "prompt_1": [ComfyJobState.OOM],
        "prompt_2": [ComfyJobState.OOM],
        "prompt_3": [ComfyJobState.COMPLETE],
        "prompt_4": [ComfyJobState.COMPLETE],
    }

    def poll_side_effect(prompt_id):
        state = poll_states[prompt_id].pop(0)
        if state == ComfyJobState.OOM:
            raise ComfyOOMError("CUDA out of memory")
        return ComfyJobResult(
            prompt_id=prompt_id,
            state=state,
            output_paths=[f"output_{prompt_id}.png"] if state == ComfyJobState.COMPLETE else [],
        )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(jobs, mock_comfy_client, batch_phase=BatchPhase.D, initial_batch_size=2)

    assert len(result.completed) == 2
    assert len(result.oom_items) == 0
    for job in result.completed:
        assert job.generation.batch_size_used in (1, 2)


def test_run_batch_oom_max_halvings_routes_to_manual_flag(mock_comfy_client):
    """OOM at batch_size=1 after 2 halvings -> MANUAL_FLAG."""
    jobs = []
    for i in range(2):
        gen = Generation(
            generation_id=i + 1,
            batch_phase=BatchPhase.C,
            agent_type="visual_prompt_agent",
            model_used="sdxl",
        )
        jobs.append(BatchJob(workflow={"prompt": f"test_{i}"}, generation=gen))

    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(6)]  # 2 + 2 + 2

    # All attempts OOM
    poll_states = {
        "prompt_0": [ComfyJobState.OOM, ComfyJobState.OOM, ComfyJobState.OOM],
        "prompt_1": [ComfyJobState.OOM, ComfyJobState.OOM, ComfyJobState.OOM],
        "prompt_2": [ComfyJobState.OOM],
        "prompt_3": [ComfyJobState.OOM],
        "prompt_4": [ComfyJobState.OOM],
        "prompt_5": [ComfyJobState.OOM],
    }

    def poll_side_effect(prompt_id):
        state = poll_states[prompt_id].pop(0)
        if state == ComfyJobState.OOM:
            raise ComfyOOMError("CUDA out of memory")
        return ComfyJobResult(prompt_id=prompt_id, state=state, output_paths=[])

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=4)

    assert len(result.completed) == 0
    assert len(result.oom_items) == 2
    for job in result.oom_items:
        assert job.generation.status == GenerationStatus.MANUAL_FLAG


def test_run_batch_oom_single_job_at_batch_size_one(mock_comfy_client):
    """Single job OOMs even at batch_size=1 -> MANUAL_FLAG."""
    gen = Generation(
        generation_id=1,
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
    )
    jobs = [BatchJob(workflow={"prompt": "test"}, generation=gen)]

    mock_comfy_client.submit.side_effect = ["prompt_0", "prompt_1", "prompt_2"]  # 3 attempts

    poll_states = {
        "prompt_0": [ComfyJobState.OOM],
        "prompt_1": [ComfyJobState.OOM],
        "prompt_2": [ComfyJobState.OOM],
    }

    def poll_side_effect(prompt_id):
        state = poll_states[prompt_id].pop(0)
        raise ComfyOOMError("CUDA out of memory")

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        result = run_batch(jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=1)

    assert len(result.completed) == 0
    assert len(result.oom_items) == 1
    assert result.oom_items[0].generation.status == GenerationStatus.MANUAL_FLAG


# ---------------------------------------------------------------------------
# run_batch tests — batch_size_used tracking
# ---------------------------------------------------------------------------

def test_run_batch_tracks_batch_size_used(mock_comfy_client, sample_jobs):
    """Every Generation record gets batch_size_used set."""
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(4)]

    def poll_side_effect(prompt_id):
        return ComfyJobResult(
            prompt_id=prompt_id,
            state=ComfyJobState.COMPLETE,
            output_paths=[f"output_{prompt_id}.png"],
        )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        run_batch(sample_jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=4)

    for job in sample_jobs:
        assert job.generation.batch_size_used == 4


def test_run_batch_tracks_batch_size_used_after_halving(mock_comfy_client):
    """Generation records track the batch size at which they completed."""
    jobs = []
    for i in range(3):
        gen = Generation(
            generation_id=i + 1,
            batch_phase=BatchPhase.C,
            agent_type="visual_prompt_agent",
            model_used="sdxl",
        )
        jobs.append(BatchJob(workflow={"prompt": f"test_{i}"}, generation=gen))

    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(5)]  # 3 + 2 retry

    # First batch (size 3): job 0 OOM, jobs 1,2 COMPLETE
    # Retry batch (size 1): job 0 COMPLETE
    poll_states = {
        "prompt_0": [ComfyJobState.OOM, ComfyJobState.COMPLETE],
        "prompt_1": [ComfyJobState.COMPLETE],
        "prompt_2": [ComfyJobState.COMPLETE],
        "prompt_3": [ComfyJobState.COMPLETE],
        "prompt_4": [ComfyJobState.COMPLETE],
    }

    def poll_side_effect(prompt_id):
        state = poll_states[prompt_id].pop(0)
        if state == ComfyJobState.OOM:
            raise ComfyOOMError("OOM")
        return ComfyJobResult(prompt_id=prompt_id, state=state, output_paths=["out.png"])

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        run_batch(jobs, mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=3)

    # Job 0 completed at batch_size=1 (after retry)
    assert jobs[0].generation.batch_size_used == 1
    # Jobs 1,2 completed at batch_size=3
    assert jobs[1].generation.batch_size_used == 3
    assert jobs[2].generation.batch_size_used == 3


# ---------------------------------------------------------------------------
# run_batch tests — timeout handling
# ---------------------------------------------------------------------------

def test_run_batch_timeout(mock_comfy_client, sample_jobs):
    """Jobs that don't complete within max_wait_s are marked FAILED."""
    mock_comfy_client.submit.side_effect = [f"prompt_{i}" for i in range(2)]

    def poll_side_effect(prompt_id):
        return ComfyJobResult(
            prompt_id=prompt_id,
            state=ComfyJobState.RUNNING,
            output_paths=[],
        )

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.monotonic", side_effect=[0, 5, 10, 15, 20, 25, 30]):
        with patch("orchestration.batch_executor.time.sleep"):
            result = run_batch(sample_jobs[:2], mock_comfy_client, batch_phase=BatchPhase.C, initial_batch_size=2, max_wait_s=10)

    assert len(result.completed) == 0
    assert len(result.failed) == 2
    for job, err in result.failed:
        assert job.generation.status == GenerationStatus.FAILED
        assert "Timeout" in err


# ---------------------------------------------------------------------------
# _poll_batch tests
# ---------------------------------------------------------------------------

def test_poll_batch_basic(mock_comfy_client):
    """_poll_batch polls until all jobs complete."""
    job1 = BatchJob(workflow={}, generation=Generation(batch_phase=BatchPhase.C, agent_type="t", model_used="m"))
    job2 = BatchJob(workflow={}, generation=Generation(batch_phase=BatchPhase.C, agent_type="t", model_used="m"))

    prompt_ids = [(job1, "p1"), (job2, "p2")]

    # First call: both RUNNING, second: p1 COMPLETE, p2 RUNNING, third: both COMPLETE
    call_count = 0
    def poll_side_effect(prompt_id):
        nonlocal call_count
        if call_count == 0:
            call_count += 1
            return ComfyJobResult(prompt_id=prompt_id, state=ComfyJobState.RUNNING, output_paths=[])
        elif call_count == 1:
            call_count += 1
            if prompt_id == "p1":
                return ComfyJobResult(prompt_id=prompt_id, state=ComfyJobState.COMPLETE, output_paths=["out.png"])
            return ComfyJobResult(prompt_id=prompt_id, state=ComfyJobState.RUNNING, output_paths=[])
        else:
            return ComfyJobResult(prompt_id=prompt_id, state=ComfyJobState.COMPLETE, output_paths=["out.png"])

    mock_comfy_client.poll_once.side_effect = poll_side_effect

    with patch("orchestration.batch_executor.time.sleep"):
        results = _poll_batch(mock_comfy_client, prompt_ids, poll_interval_s=0.1, max_wait_s=10)

    assert len(results) == 2
    for job, result in results:
        assert result.state == ComfyJobState.COMPLETE


def test_poll_batch_oom_raises(mock_comfy_client):
    """_poll_batch catches ComfyOOMError and returns OOM state."""
    job = BatchJob(workflow={}, generation=Generation(batch_phase=BatchPhase.C, agent_type="t", model_used="m"))

    mock_comfy_client.poll_once.side_effect = ComfyOOMError("OOM")

    with patch("orchestration.batch_executor.time.sleep"):
        results = _poll_batch(mock_comfy_client, [(job, "p1")], poll_interval_s=0.1, max_wait_s=10)

    assert len(results) == 1
    assert results[0][1].state == ComfyJobState.OOM
    assert "OOM" in results[0][1].error_message


# ---------------------------------------------------------------------------
# submit_and_poll_single tests
# ---------------------------------------------------------------------------

def test_submit_and_poll_single(mock_comfy_client):
    """Convenience function submits and polls a single job."""
    gen = Generation(batch_phase=BatchPhase.C, agent_type="t", model_used="m")
    workflow = {"prompt": "test"}

    mock_comfy_client.submit.return_value = "prompt_123"
    mock_comfy_client.poll_until_complete.return_value = ComfyJobResult(
        prompt_id="prompt_123",
        state=ComfyJobState.COMPLETE,
        output_paths=["output.png"],
    )

    result = submit_and_poll_single(mock_comfy_client, workflow, gen)

    assert result.state == ComfyJobState.COMPLETE
    assert gen.comfyui_prompt_id == "prompt_123"
    assert gen.status == GenerationStatus.RUNNING


# ---------------------------------------------------------------------------
# BatchJob and BatchResult dataclass tests
# ---------------------------------------------------------------------------

def test_batch_job_dataclass():
    gen = Generation(batch_phase=BatchPhase.C, agent_type="t", model_used="m")
    job = BatchJob(workflow={"a": 1}, generation=gen)
    assert job.workflow == {"a": 1}
    assert job.generation is gen
    assert job.mutation_fn is None
    assert job.mutation_kwargs is None


def test_batch_result_dataclass():
    result = BatchResult(completed=[], failed=[], oom_items=[])
    assert result.completed == []
    assert result.failed == []
    assert result.oom_items == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])