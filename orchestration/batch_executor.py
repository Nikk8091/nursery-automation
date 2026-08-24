"""
orchestration/batch_executor.py — Phase-boundary discipline + adaptive OOM batch-halving

Owning spec: docs/specs/spec_01_vram_architecture.md §4
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

from db.models import BatchPhase, Generation, GenerationStatus
from engine.comfy_client import ComfyJobResult, ComfyJobState, ComfyOOMError, ComfyUIClient


@dataclass
class BatchJob:
    """A single generation job to be executed in a batch.

    Attributes:
        workflow: The mutated ComfyUI workflow graph ready for submission.
        generation: The Generation record to update with results.
        mutation_fn: Optional callable to re-mutate the workflow on retry
                     (e.g., with adjusted seed or motion_strength).
        mutation_kwargs: Keyword arguments passed to mutation_fn on retry.
    """
    workflow: dict[str, Any]
    generation: Generation
    mutation_fn: Optional[Callable[..., dict[str, Any]]] = None
    mutation_kwargs: Optional[dict[str, Any]] = None


@dataclass
class BatchResult:
    """Result of executing a batch of jobs."""
    completed: list[BatchJob]
    failed: list[tuple[BatchJob, str]]
    oom_items: list[BatchJob]


def run_batch(
    jobs: list[BatchJob],
    client: ComfyUIClient,
    *,
    batch_phase: BatchPhase,
    initial_batch_size: int = 4,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 600.0,
) -> BatchResult:
    """Execute a batch of ComfyUI jobs with adaptive OOM halving.

    Implements the algorithm from spec_01 §4:
    - Start with `initial_batch_size` concurrent jobs.
    - On OOM: halve remaining batch size, resubmit remaining items.
    - Maximum 2 halvings (full -> 1/2 -> 1/4).
    - If still OOM at batch_size == 1 after 2 halvings: mark MANUAL_FLAG.

    Args:
        jobs: List of BatchJob items to execute.
        client: ComfyUIClient instance for submission and polling.
        batch_phase: The BatchPhase (C or D) this batch belongs to.
        initial_batch_size: Starting concurrent job count.
        poll_interval_s: Seconds between polling each job.
        max_wait_s: Maximum seconds to wait for a single job.

    Returns:
        BatchResult with completed jobs, failed jobs (with error messages),
        and items that hit OOM at minimum batch size.
    """
    remaining_jobs = list(jobs)
    completed: list[BatchJob] = []
    failed: list[tuple[BatchJob, str]] = []
    oom_items: list[BatchJob] = []

    batch_size = initial_batch_size
    halving_attempt = 0

    while remaining_jobs:
        # Submit up to batch_size jobs
        current_batch = remaining_jobs[:batch_size]
        remaining_jobs = remaining_jobs[batch_size:]

        # Submit all jobs in current batch
        prompt_ids: list[tuple[BatchJob, str]] = []
        for job in current_batch:
            prompt_id = client.submit(job.workflow)
            job.generation.comfyui_prompt_id = prompt_id
            job.generation.status = GenerationStatus.RUNNING
            job.generation.batch_size_used = batch_size
            prompt_ids.append((job, prompt_id))

        # Poll all jobs in this batch until completion or OOM
        batch_results = _poll_batch(
            client=client,
            prompt_ids=prompt_ids,
            poll_interval_s=poll_interval_s,
            max_wait_s=max_wait_s,
        )

        # Process results
        oom_in_batch = False
        for job, result in batch_results:
            if result.state == ComfyJobState.COMPLETE:
                job.generation.status = GenerationStatus.COMPLETE
                completed.append(job)
            elif result.state == ComfyJobState.OOM:
                oom_in_batch = True
                if halving_attempt < 2:
                    # Will retry with halved batch size (up to 2 halvings total)
                    job.generation.status = GenerationStatus.OOM
                    job.generation.retry_count += 1
                    remaining_jobs.insert(0, job)
                else:
                    # Max halvings reached
                    job.generation.status = GenerationStatus.MANUAL_FLAG
                    oom_items.append(job)
            elif result.state == ComfyJobState.FAILED:
                job.generation.status = GenerationStatus.FAILED
                failed.append((job, result.error_message or "Unknown error"))
            else:
                # Should not happen with poll_until_complete, but handle gracefully
                job.generation.status = GenerationStatus.FAILED
                failed.append((job, f"Unexpected state: {result.state}"))

        if oom_in_batch:
            if halving_attempt < 2:
                # Halve batch size (but not below 1) and retry
                batch_size = max(1, batch_size // 2)
                halving_attempt += 1
                # Note: remaining_jobs already has OOM jobs prepended above
            else:
                # Max halvings reached - remaining OOM jobs already marked MANUAL_FLAG
                pass

    return BatchResult(completed=completed, failed=failed, oom_items=oom_items)


def _poll_batch(
    client: ComfyUIClient,
    prompt_ids: list[tuple[BatchJob, str]],
    *,
    poll_interval_s: float,
    max_wait_s: float,
) -> list[tuple[BatchJob, ComfyJobResult]]:
    """Poll a batch of jobs until all complete, fail, or OOM."""
    results: list[tuple[BatchJob, ComfyJobResult]] = []
    # Use prompt_id as key (hashable) instead of BatchJob
    pending = {prompt_id: job for job, prompt_id in prompt_ids}
    start_time = time.monotonic()

    while pending:
        elapsed = time.monotonic() - start_time
        if elapsed >= max_wait_s:
            for prompt_id, job in list(pending.items()):
                job.generation.status = GenerationStatus.FAILED
                results.append((
                    job,
                    ComfyJobResult(
                        prompt_id=prompt_id,
                        state=ComfyJobState.FAILED,
                        output_paths=[],
                        error_message=f"Timeout after {max_wait_s}s",
                    ),
                ))
            break

        for prompt_id, job in list(pending.items()):
            try:
                result = client.poll_once(prompt_id)
            except ComfyOOMError as e:
                # OOM detected - mark and remove from pending
                results.append((
                    job,
                    ComfyJobResult(
                        prompt_id=prompt_id,
                        state=ComfyJobState.OOM,
                        output_paths=[],
                        error_message=str(e),
                    ),
                ))
                del pending[prompt_id]
                continue

            if result.state == ComfyJobState.COMPLETE:
                results.append((job, result))
                del pending[prompt_id]
            elif result.state == ComfyJobState.FAILED:
                results.append((job, result))
                del pending[prompt_id]
            elif result.state == ComfyJobState.OOM:
                # poll_once raises ComfyOOMError instead of returning OOM state,
                # but handle this case defensively
                results.append((job, result))
                del pending[prompt_id]
            # RUNNING and QUEUED stay in pending

        if pending:
            time.sleep(poll_interval_s)

    return results


def submit_and_poll_single(
    client: ComfyUIClient,
    workflow: dict[str, Any],
    generation: Generation,
    *,
    poll_interval_s: float = 2.0,
    max_wait_s: float = 600.0,
) -> ComfyJobResult:
    """Convenience: submit a single workflow and poll to completion.

    This does NOT implement OOM halving — use run_batch() for batches
    that need adaptive sizing. This is for one-off jobs (e.g., thumbnail).
    """
    prompt_id = client.submit(workflow)
    generation.comfyui_prompt_id = prompt_id
    generation.status = GenerationStatus.RUNNING
    return client.poll_until_complete(prompt_id, poll_interval_s=poll_interval_s, max_wait_s=max_wait_s)