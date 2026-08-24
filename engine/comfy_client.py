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

import json
import time
from copy import deepcopy
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Optional

import httpx

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
        self.base_url = base_url.rstrip("/")
        self.timeout_s = timeout_s
        self._client = httpx.Client(timeout=timeout_s)
        self._workflow_cache: dict[Path, dict[str, Any]] = {}

    def load_workflow_template(self, template_path: Path) -> dict[str, Any]:
        """Load a versioned workflow JSON template from comfyui/workflows/.

        Returns a deep-copyable dict. Callers MUST deep-copy this before
        mutating (spec_04 §3, step 2) — never mutate the cached template.
        """
        if template_path not in self._workflow_cache:
            with template_path.open("r", encoding="utf-8") as f:
                self._workflow_cache[template_path] = json.load(f)
        return deepcopy(self._workflow_cache[template_path])

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
        mutated = deepcopy(template)

        for node_id, node in mutated.items():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            if class_type == "CLIPTextEncode":
                text = inputs.get("text", "")
                if "__SCENE_PROMPT__" in text:
                    inputs["text"] = positive_prompt
                elif "__THUMBNAIL_PROMPT__" in text:
                    inputs["text"] = positive_prompt
                elif "__NEGATIVE_PROMPT_LOCK__" in text:
                    inputs["text"] = negative_prompt

            elif class_type == "KSampler":
                if "seed" in inputs:
                    inputs["seed"] = seed

            elif class_type == "IPAdapterApply":
                if "weight" in inputs:
                    inputs["weight"] = ip_adapter_weight
                if "image" in inputs and ip_adapter_reference_paths:
                    inputs["image"] = ip_adapter_reference_paths[0].as_posix()

            elif class_type == "SaveImage":
                if "filename_prefix" in inputs:
                    inputs["filename_prefix"] = filename_prefix

        return mutated

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
        mutated = deepcopy(template)

        for node_id, node in mutated.items():
            if not isinstance(node, dict):
                continue
            class_type = node.get("class_type", "")
            inputs = node.get("inputs", {})

            if class_type == "LoadImage":
                if "image" in inputs:
                    inputs["image"] = conditioning_image_path.as_posix()

            elif class_type == "CLIPTextEncode":
                text = inputs.get("text", "")
                if "__MOTION_PROMPT__" in text:
                    inputs["text"] = motion_prompt

            elif class_type == "KSampler":
                if "seed" in inputs:
                    inputs["seed"] = seed
                if "motion_strength" in inputs:
                    inputs["motion_strength"] = motion_strength

            elif class_type == "SaveVideo":
                if "filename_prefix" in inputs:
                    inputs["filename_prefix"] = filename_prefix

        return mutated

    def submit(self, mutated_workflow: dict[str, Any]) -> str:
        """POST /prompt with the mutated graph.

        Returns the ComfyUI-assigned prompt_id. Callers MUST persist this
        to Generation.comfyui_prompt_id immediately (before polling begins)
        for crash-resumability (spec_01 §3).
        """
        response = self._client.post(
            f"{self.base_url}/prompt",
            json={"prompt": mutated_workflow},
        )
        response.raise_for_status()
        data = response.json()
        return data["prompt_id"]

    def poll_once(self, prompt_id: str) -> ComfyJobResult:
        """Single GET /history/{prompt_id} poll. Does not block/loop.

        Raises:
            ComfyOOMError: if the job reports an OOM condition. Caller
                (batch executor, spec_01 §4) is responsible for the
                halve-and-resubmit loop — this method does not retry.
        """
        response = self._client.get(f"{self.base_url}/history/{prompt_id}")
        response.raise_for_status()
        history = response.json()

        if prompt_id not in history:
            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.QUEUED,
                output_paths=[],
            )

        job_data = history[prompt_id]
        status = job_data.get("status", {})

        # Check for OOM via status_str first
        status_str = status.get("status_str", "").lower()
        if status_str == "oom":
            error_msg = status.get("error", {}).get("message", "Out of memory")
            raise ComfyOOMError(error_msg)

        if status.get("completed", False):
            outputs = job_data.get("outputs", {})
            output_paths = []
            for node_output in outputs.values():
                if "images" in node_output:
                    for img in node_output["images"]:
                        output_paths.append(Path(img["filename"]))
                if "videos" in node_output:
                    for vid in node_output["videos"]:
                        output_paths.append(Path(vid["filename"]))

            # Check if there's an error in the completed job
            if "error" in status:
                error_msg = status.get("error", {}).get("message", "Unknown error")
                if "out of memory" in error_msg.lower() or "oom" in error_msg.lower():
                    raise ComfyOOMError(error_msg)
                return ComfyJobResult(
                    prompt_id=prompt_id,
                    state=ComfyJobState.FAILED,
                    output_paths=[],
                    error_message=error_msg,
                )

            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.COMPLETE,
                output_paths=output_paths,
            )

        # Check for error status before completion
        if status_str == "error" or "error" in status:
            error_msg = status.get("error", {}).get("message", "Unknown error")
            if "out of memory" in error_msg.lower() or "oom" in error_msg.lower():
                raise ComfyOOMError(error_msg)
            return ComfyJobResult(
                prompt_id=prompt_id,
                state=ComfyJobState.FAILED,
                output_paths=[],
                error_message=error_msg,
            )

        return ComfyJobResult(
            prompt_id=prompt_id,
            state=ComfyJobState.RUNNING,
            output_paths=[],
        )

    def poll_until_complete(
        self, prompt_id: str, *, poll_interval_s: float = 2.0, max_wait_s: float = 600.0
    ) -> ComfyJobResult:
        """Convenience wrapper: polls poll_once() on an interval until
        COMPLETE/FAILED, or raises ComfyOOMError, or raises TimeoutError
        at max_wait_s. Most agent code should use this rather than
        poll_once() directly; the batch executor uses poll_once() when it
        needs to interleave polling across many concurrent jobs.
        """
        start_time = time.monotonic()
        while True:
            result = self.poll_once(prompt_id)
            if result.state in (ComfyJobState.COMPLETE, ComfyJobState.FAILED):
                return result
            if result.state == ComfyJobState.OOM:
                raise ComfyOOMError(result.error_message or "OOM detected")

            elapsed = time.monotonic() - start_time
            if elapsed >= max_wait_s:
                raise TimeoutError(f"Job {prompt_id} did not complete within {max_wait_s}s")

            time.sleep(poll_interval_s)

    def fetch_output(self, result: ComfyJobResult, destination_dir: Path) -> list[Path]:
        """GET /view for each output in a completed ComfyJobResult, saving
        to destination_dir. Returns the local file paths (these become
        Asset.file_path values — spec_02 §3).
        """
        destination_dir.mkdir(parents=True, exist_ok=True)
        local_paths = []

        for output_path in result.output_paths:
            filename = output_path.name
            response = self._client.get(
                f"{self.base_url}/view",
                params={"filename": filename, "type": "output"},
            )
            response.raise_for_status()

            local_path = destination_dir / filename
            with local_path.open("wb") as f:
                f.write(response.content)
            local_paths.append(local_path)

        return local_paths

    def queue_depth(self) -> int:
        """GET /queue — used by the batch executor for completion
        bookkeeping across a large in-flight batch."""
        response = self._client.get(f"{self.base_url}/queue")
        response.raise_for_status()
        data = response.json()

        running = len(data.get("queue_running", []))
        pending = len(data.get("queue_pending", []))
        return running + pending

    def is_model_resident(self, checkpoint_name: str) -> bool:
        """Best-effort check of whether the given checkpoint is the
        currently-loaded model, for asserting the Phase C/D model-swap
        boundary discipline in tests (spec_01 §2)."""
        try:
            response = self._client.get(f"{self.base_url}/system/stats")
            response.raise_for_status()
            stats = response.json()
            loaded_models = stats.get("models", {}).get("checkpoints", [])
            return checkpoint_name in loaded_models
        except Exception:
            return False

    def close(self) -> None:
        """Close the underlying HTTP client."""
        self._client.close()

    def __enter__(self) -> ComfyUIClient:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.close()