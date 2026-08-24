"""
engine/comfy_client.py — Headless ComfyUI REST API client.

Source of truth: docs/specs/spec_01_vram_architecture.md §3, §4
                  docs/specs/spec_04_comfyui_workflows.md §3

This is an INTERFACE STUB (Module 2 — the module to build immediately after
db/models.py). Method bodies are intentionally unimplemented; this defines
the exact contract every agent (VisualPromptAgent, VideoGenAgent) codes
against.

Rules enforced by this module's design (see CLAUDE.md / .cursorrules):
  - This is the ONLY module in the codebase permitted to speak HTTP to
    ComfyUI. No other module may import `requests`/`httpx` to hit the
    ComfyUI daemon directly.
  - No torch/diffusers imports anywhere in this file — this is a pure
    HTTP client, not an inference engine.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

from db.models import BatchPhase


class ComfyJobState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    OOM = "oom"


@dataclass
class ComfyJobResult:
    prompt_id: str
    state: ComfyJobState
    output_paths: list[Path]
    error_message: Optional[str] = None


class ComfyOOMError(Exception):
    """Raised when a polled job reports an out-of-memory condition.
    Callers (VisualPromptAgent/VideoGenAgent) catch this to trigger the
    adaptive batch-halving fallback per spec_01 §4."""


class ComfyUIClient:
    """Thin REST wrapper around a headless ComfyUI instance.

    One instance of this client is shared per pipeline run. It never
    tracks GPU/VRAM state itself — that's ComfyUI's job (spec_01 §3).
    This client's only responsibilities: submit mutated workflow graphs,
    poll for completion, retrieve output, and surface OOM conditions to
    the caller in a typed way.
    """

    def __init__(self, base_url: str = "http://localhost:8188", timeout_s: int = 300) -> None:
        """
        Args:
            base_url: ComfyUI daemon address, e.g. http://localhost:8188
            timeout_s: per-request HTTP timeout (not the job-completion timeout —
                       see poll_until_complete for that).
        """
        raise NotImplementedError

    def load_workflow_template(self, template_path: Path) -> dict[str, Any]:
        """Load a versioned workflow JSON template from comfyui/workflows/.

        Returns a deep-copyable dict. Callers MUST deep-copy this before
        mutating (spec_04 §3, step 2) — never mutate the cached template.
        """
        raise NotImplementedError

    def mutate_still_workflow(
        self,
        template: dict[str, Any],
        *,
        positive_prompt: str,
        negative_prompt: str,
        seed: int,
        ip_adapter_reference_paths: list[Path],
        ip_adapter_weight: float,
        filename_prefix: str,
    ) -> dict[str, Any]:
        """Apply the mutation points defined in spec_04 §2.1 to an
        sdxl_still.json (or thumbnail_still.json) template copy.
        Returns the mutated graph, ready to submit.
        """
        raise NotImplementedError

    def mutate_video_workflow(
        self,
        template: dict[str, Any],
        *,
        conditioning_image_path: Path,
        motion_prompt: str,
        seed: int,
        motion_strength: float,
        filename_prefix: str,
    ) -> dict[str, Any]:
        """Apply the mutation points defined in spec_04 §2.3 to a
        wan22_i2v.json template copy. `motion_strength` is reduced 20%
        per retry attempt by the calling agent (spec_03 §4.2), not by
        this client.
        """
        raise NotImplementedError

    def submit(self, mutated_workflow: dict[str, Any]) -> str:
        """POST /prompt with the mutated graph.

        Returns the ComfyUI-assigned prompt_id. Callers MUST persist this
        to Generation.comfyui_prompt_id immediately (before polling begins)
        for crash-resumability (spec_01 §3).
        """
        raise NotImplementedError

    def poll_once(self, prompt_id: str) -> ComfyJobResult:
        """Single GET /history/{prompt_id} poll. Does not block/loop.

        Raises:
            ComfyOOMError: if the job reports an OOM condition. Caller
                (batch executor, spec_01 §4) is responsible for the
                halve-and-resubmit loop — this method does not retry.
        """
        raise NotImplementedError

    def poll_until_complete(
        self, prompt_id: str, *, poll_interval_s: float = 2.0, max_wait_s: float = 600.0
    ) -> ComfyJobResult:
        """Convenience wrapper: polls poll_once() on an interval until
        COMPLETE/FAILED, or raises ComfyOOMError, or raises TimeoutError
        at max_wait_s. Most agent code should use this rather than
        poll_once() directly; the batch executor uses poll_once() when it
        needs to interleave polling across many concurrent jobs.
        """
        raise NotImplementedError

    def fetch_output(self, result: ComfyJobResult, destination_dir: Path) -> list[Path]:
        """GET /view for each output in a completed ComfyJobResult, saving
        to destination_dir. Returns the local file paths (these become
        Asset.file_path values — spec_02 §3).
        """
        raise NotImplementedError

    def queue_depth(self) -> int:
        """GET /queue — used by the batch executor for completion
        bookkeeping across a large in-flight batch."""
        raise NotImplementedError

    def is_model_resident(self, checkpoint_name: str) -> bool:
        """Best-effort check of whether the given checkpoint is the
        currently-loaded model, for asserting the Phase C/D model-swap
        boundary discipline in tests (spec_01 §2)."""
        raise NotImplementedError
