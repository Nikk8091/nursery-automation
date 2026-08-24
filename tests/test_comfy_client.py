"""
tests/test_comfy_client.py — Unit tests for engine/comfy_client.py (mocked HTTP)

Owning spec: docs/specs/spec_01_vram_architecture.md
Covers: workflow loading, mutation, submit, polling, OOM detection, output fetching,
        queue depth, model residency check, context manager, error handling.
"""

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, Mock

import pytest
import httpx

from engine.comfy_client import (
    ComfyUIClient,
    ComfyJobState,
    ComfyJobResult,
    ComfyOOMError,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_httpx_client_class():
    """Create a mock httpx.Client class for testing."""
    with patch("httpx.Client") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        yield mock_client_class


@pytest.fixture
def mock_httpx_client(mock_httpx_client_class):
    """Create a mock httpx.Client instance for testing."""
    return mock_httpx_client_class.return_value


@pytest.fixture
def comfy_client(mock_httpx_client_class):
    """Create a ComfyUIClient with mocked HTTP."""
    client = ComfyUIClient(base_url="http://localhost:8188", timeout_s=60)
    yield client
    client.close()


@pytest.fixture
def sdxl_still_template():
    """Sample SDXL still workflow template matching comfyui/workflows/sdxl_still.json."""
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 481920,
                "steps": 30,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base_1.0.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1024, "height": 1024, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "__SCENE_PROMPT__", "clip": ["4", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "__NEGATIVE_PROMPT_LOCK__", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "IPAdapterApply",
            "inputs": {
                "weight": 0.65,
                "ipadapter": ["9", 0],
                "image": ["10", 0],
                "model": ["4", 0],
            },
        },
        "11": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "12": {
            "class_type": "SaveImage",
            "inputs": {"images": ["11", 0], "filename_prefix": "__EPISODE_SCENE_ID__"},
        },
    }


@pytest.fixture
def thumbnail_still_template():
    """Sample thumbnail workflow template matching comfyui/workflows/thumbnail_still.json."""
    return {
        "3": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 481920,
                "steps": 30,
                "cfg": 7.0,
                "sampler_name": "dpmpp_2m",
                "scheduler": "karras",
                "denoise": 1.0,
                "model": ["4", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["5", 0],
            },
        },
        "4": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "sdxl_base_1.0.safetensors"},
        },
        "5": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": 1280, "height": 720, "batch_size": 1},
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {
                "text": "__THUMBNAIL_PROMPT__, bold clear composition, high contrast, readable at small size",
                "clip": ["4", 1],
            },
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "__NEGATIVE_PROMPT_LOCK__", "clip": ["4", 1]},
        },
        "8": {
            "class_type": "IPAdapterApply",
            "inputs": {
                "weight": 0.65,
                "ipadapter": ["9", 0],
                "image": ["10", 0],
                "model": ["4", 0],
            },
        },
        "11": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["3", 0], "vae": ["4", 2]},
        },
        "12": {
            "class_type": "SaveImage",
            "inputs": {"images": ["11", 0], "filename_prefix": "__EPISODE_ID___thumbnail"},
        },
    }


@pytest.fixture
def wan22_i2v_template():
    """Sample Wan 2.2 I2V workflow template matching comfyui/workflows/wan22_i2v.json."""
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": "wan2.2_ti2v_5b.safetensors"},
        },
        "2": {
            "class_type": "LoadImage",
            "inputs": {"image": "__APPROVED_STILL_PATH__"},
        },
        "3": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": "__MOTION_PROMPT__", "clip": ["1", 1]},
        },
        "4": {
            "class_type": "KSampler",
            "inputs": {
                "seed": 481920,
                "motion_strength": 0.8,
                "model": ["1", 0],
                "positive": ["3", 0],
                "conditioning_image": ["2", 0],
            },
        },
        "5": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["4", 0], "vae": ["1", 2]},
        },
        "6": {
            "class_type": "SaveVideo",
            "inputs": {"video": ["5", 0], "fps": 24, "filename_prefix": "__EPISODE_SCENE_ID__"},
        },
    }


# ---------------------------------------------------------------------------
# __init__ and context manager tests
# ---------------------------------------------------------------------------

def test_init_sets_base_url_and_timeout(mock_httpx_client_class):
    client = ComfyUIClient(base_url="http://custom:9090", timeout_s=120)
    assert client.base_url == "http://custom:9090"
    assert client.timeout_s == 120
    mock_httpx_client_class.assert_called_once_with(timeout=120)
    client.close()


def test_init_strips_trailing_slash(mock_httpx_client):
    client = ComfyUIClient(base_url="http://localhost:8188/")
    assert client.base_url == "http://localhost:8188"
    client.close()


def test_context_manager(mock_httpx_client_class):
    mock_client = mock_httpx_client_class.return_value
    with ComfyUIClient() as client:
        assert isinstance(client, ComfyUIClient)
    mock_client.close.assert_called_once()


# ---------------------------------------------------------------------------
# load_workflow_template tests
# ---------------------------------------------------------------------------

def test_load_workflow_template_loads_and_caches(comfy_client, sdxl_still_template):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sdxl_still_template, f)
        template_path = Path(f.name)

    try:
        # First load
        result1 = comfy_client.load_workflow_template(template_path)
        assert result1 == sdxl_still_template

        # Second load should return cached copy (but deep-copied)
        result2 = comfy_client.load_workflow_template(template_path)
        assert result2 == sdxl_still_template
        assert result1 is not result2  # Deep copied
    finally:
        template_path.unlink()


def test_load_workflow_template_deep_copies(comfy_client, sdxl_still_template):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sdxl_still_template, f)
        template_path = Path(f.name)

    try:
        result1 = comfy_client.load_workflow_template(template_path)
        result2 = comfy_client.load_workflow_template(template_path)

        # Mutate result1 - should not affect result2
        result1["3"]["inputs"]["seed"] = 999999
        assert result2["3"]["inputs"]["seed"] == 481920
    finally:
        template_path.unlink()


def test_load_workflow_template_file_not_found(comfy_client):
    with pytest.raises(FileNotFoundError):
        comfy_client.load_workflow_template(Path("/nonexistent/workflow.json"))


# ---------------------------------------------------------------------------
# mutate_still_workflow tests (spec_04 §2.1)
# ---------------------------------------------------------------------------

def test_mutate_still_workflow_positive_prompt(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="A cute duckling in a pond",
        negative_prompt="ugly, deformed",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    # Find the positive CLIPTextEncode node
    positive_node = None
    for node in mutated.values():
        if node.get("class_type") == "CLIPTextEncode":
            text = node.get("inputs", {}).get("text", "")
            if "__SCENE_PROMPT__" in text or text == "A cute duckling in a pond":
                positive_node = node
                break

    assert positive_node is not None
    assert positive_node["inputs"]["text"] == "A cute duckling in a pond"


def test_mutate_still_workflow_negative_prompt(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test prompt",
        negative_prompt="photorealistic, extra limbs, sharp teeth",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    negative_node = None
    for node in mutated.values():
        if node.get("class_type") == "CLIPTextEncode":
            text = node.get("inputs", {}).get("text", "")
            if "__NEGATIVE_PROMPT_LOCK__" in text or "photorealistic" in text:
                negative_node = node
                break

    assert negative_node is not None
    assert negative_node["inputs"]["text"] == "photorealistic, extra limbs, sharp teeth"


def test_mutate_still_workflow_seed(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test",
        negative_prompt="test",
        seed=999999,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    ksampler_node = None
    for node in mutated.values():
        if node.get("class_type") == "KSampler":
            ksampler_node = node
            break

    assert ksampler_node is not None
    assert ksampler_node["inputs"]["seed"] == 999999


def test_mutate_still_workflow_ip_adapter_weight(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test",
        negative_prompt="test",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.85,
        filename_prefix="ep001_scene01",
    )

    ip_adapter_node = None
    for node in mutated.values():
        if node.get("class_type") == "IPAdapterApply":
            ip_adapter_node = node
            break

    assert ip_adapter_node is not None
    assert ip_adapter_node["inputs"]["weight"] == 0.85


def test_mutate_still_workflow_ip_adapter_reference_image(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test",
        negative_prompt="test",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png"), Path("/refs/duckling_ref2.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    ip_adapter_node = None
    for node in mutated.values():
        if node.get("class_type") == "IPAdapterApply":
            ip_adapter_node = node
            break

    assert ip_adapter_node is not None
    # Should use the first reference image (using as_posix for cross-platform compatibility)
    assert ip_adapter_node["inputs"]["image"] == Path("/refs/duckling_ref.png").as_posix()


def test_mutate_still_workflow_filename_prefix(comfy_client, sdxl_still_template):
    mutated = comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test",
        negative_prompt="test",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    save_node = None
    for node in mutated.values():
        if node.get("class_type") == "SaveImage":
            save_node = node
            break

    assert save_node is not None
    assert save_node["inputs"]["filename_prefix"] == "ep001_scene01"


def test_mutate_still_workflow_does_not_mutate_original(comfy_client, sdxl_still_template):
    original_seed = sdxl_still_template["3"]["inputs"]["seed"]

    comfy_client.mutate_still_workflow(
        sdxl_still_template,
        positive_prompt="test",
        negative_prompt="test",
        seed=999999,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_scene01",
    )

    # Original should be unchanged
    assert sdxl_still_template["3"]["inputs"]["seed"] == original_seed


def test_mutate_still_workflow_thumbnail_prompt(comfy_client, thumbnail_still_template):
    mutated = comfy_client.mutate_still_workflow(
        thumbnail_still_template,
        positive_prompt="Bold thumbnail composition, high contrast",
        negative_prompt="ugly, deformed",
        seed=12345,
        ip_adapter_reference_paths=[Path("/refs/duckling_ref.png")],
        ip_adapter_weight=0.7,
        filename_prefix="ep001_thumbnail",
    )

    # Should handle __THUMBNAIL_PROMPT__ placeholder
    positive_node = None
    for node in mutated.values():
        if node.get("class_type") == "CLIPTextEncode":
            text = node.get("inputs", {}).get("text", "")
            if "Bold thumbnail composition" in text:
                positive_node = node
                break

    assert positive_node is not None
    assert "Bold thumbnail composition, high contrast" in positive_node["inputs"]["text"]


# ---------------------------------------------------------------------------
# mutate_video_workflow tests (spec_04 §2.3)
# ---------------------------------------------------------------------------

def test_mutate_video_workflow_conditioning_image(comfy_client, wan22_i2v_template):
    mutated = comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="duckling bobs to the beat, gentle swaying",
        seed=555555,
        motion_strength=0.7,
        filename_prefix="ep001_scene01",
    )

    load_image_node = None
    for node in mutated.values():
        if node.get("class_type") == "LoadImage":
            load_image_node = node
            break

    assert load_image_node is not None
    assert load_image_node["inputs"]["image"] == Path("/outputs/ep001_scene01_00001.png").as_posix()


def test_mutate_video_workflow_motion_prompt(comfy_client, wan22_i2v_template):
    mutated = comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="duckling bobs to the beat, gentle swaying",
        seed=555555,
        motion_strength=0.7,
        filename_prefix="ep001_scene01",
    )

    clip_node = None
    for node in mutated.values():
        if node.get("class_type") == "CLIPTextEncode":
            text = node.get("inputs", {}).get("text", "")
            if "__MOTION_PROMPT__" in text or "duckling bobs" in text:
                clip_node = node
                break

    assert clip_node is not None
    assert clip_node["inputs"]["text"] == "duckling bobs to the beat, gentle swaying"


def test_mutate_video_workflow_seed(comfy_client, wan22_i2v_template):
    mutated = comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="test motion",
        seed=777777,
        motion_strength=0.7,
        filename_prefix="ep001_scene01",
    )

    ksampler_node = None
    for node in mutated.values():
        if node.get("class_type") == "KSampler":
            ksampler_node = node
            break

    assert ksampler_node is not None
    assert ksampler_node["inputs"]["seed"] == 777777


def test_mutate_video_workflow_motion_strength(comfy_client, wan22_i2v_template):
    mutated = comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="test motion",
        seed=555555,
        motion_strength=0.5,
        filename_prefix="ep001_scene01",
    )

    ksampler_node = None
    for node in mutated.values():
        if node.get("class_type") == "KSampler":
            ksampler_node = node
            break

    assert ksampler_node is not None
    assert ksampler_node["inputs"]["motion_strength"] == 0.5


def test_mutate_video_workflow_filename_prefix(comfy_client, wan22_i2v_template):
    mutated = comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="test motion",
        seed=555555,
        motion_strength=0.7,
        filename_prefix="ep001_scene01_video",
    )

    save_video_node = None
    for node in mutated.values():
        if node.get("class_type") == "SaveVideo":
            save_video_node = node
            break

    assert save_video_node is not None
    assert save_video_node["inputs"]["filename_prefix"] == "ep001_scene01_video"


def test_mutate_video_workflow_does_not_mutate_original(comfy_client, wan22_i2v_template):
    original_seed = wan22_i2v_template["4"]["inputs"]["seed"]

    comfy_client.mutate_video_workflow(
        wan22_i2v_template,
        conditioning_image_path=Path("/outputs/ep001_scene01_00001.png"),
        motion_prompt="test motion",
        seed=888888,
        motion_strength=0.7,
        filename_prefix="ep001_scene01",
    )

    assert wan22_i2v_template["4"]["inputs"]["seed"] == original_seed


# ---------------------------------------------------------------------------
# submit tests (POST /prompt)
# ---------------------------------------------------------------------------

def test_submit_returns_prompt_id(comfy_client, mock_httpx_client, sdxl_still_template):
    mock_response = MagicMock()
    mock_response.json.return_value = {"prompt_id": "abc123def456"}
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.post.return_value = mock_response

    prompt_id = comfy_client.submit(sdxl_still_template)

    assert prompt_id == "abc123def456"
    mock_httpx_client.post.assert_called_once()
    call_args = mock_httpx_client.post.call_args
    assert call_args[0][0] == "http://localhost:8188/prompt"
    assert "prompt" in call_args[1]["json"]


def test_submit_raises_on_http_error(comfy_client, mock_httpx_client, sdxl_still_template):
    mock_response = MagicMock()
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        "500 Server Error", request=MagicMock(), response=mock_response
    )
    mock_httpx_client.post.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        comfy_client.submit(sdxl_still_template)


# ---------------------------------------------------------------------------
# poll_once tests
# ---------------------------------------------------------------------------

def test_poll_once_queued(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {}
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.poll_once("prompt_123")

    assert result.prompt_id == "prompt_123"
    assert result.state == ComfyJobState.QUEUED
    assert result.output_paths == []


def test_poll_once_running(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {"status_str": "running", "completed": False},
            "outputs": {},
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.poll_once("prompt_123")

    assert result.state == ComfyJobState.RUNNING


def test_poll_once_complete_with_images(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "12": {
                    "images": [
                        {"filename": "ep001_scene01_00001.png"},
                        {"filename": "ep001_scene01_00002.png"},
                    ]
                }
            },
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.poll_once("prompt_123")

    assert result.state == ComfyJobState.COMPLETE
    assert len(result.output_paths) == 2
    assert result.output_paths[0].name == "ep001_scene01_00001.png"
    assert result.output_paths[1].name == "ep001_scene01_00002.png"


def test_poll_once_complete_with_videos(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "6": {
                    "videos": [{"filename": "ep001_scene01_00001.mp4"}]
                }
            },
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.poll_once("prompt_123")

    assert result.state == ComfyJobState.COMPLETE
    assert len(result.output_paths) == 1
    assert result.output_paths[0].name == "ep001_scene01_00001.mp4"


def test_poll_once_failed(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {
                "completed": True,
                "status_str": "error",
                "error": {"message": "CUDA out of memory"},
            },
            "outputs": {},
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    # OOM error in completed job should raise ComfyOOMError
    with pytest.raises(ComfyOOMError):
        comfy_client.poll_once("prompt_123")


def test_poll_once_failed_non_oom(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {
                "completed": True,
                "status_str": "error",
                "error": {"message": "Model not found"},
            },
            "outputs": {},
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.poll_once("prompt_123")

    assert result.state == ComfyJobState.FAILED
    assert result.error_message == "Model not found"


def test_poll_once_oom_via_status_str(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "prompt_123": {
            "status": {"status_str": "oom", "completed": False},
            "outputs": {},
        }
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    with pytest.raises(ComfyOOMError):
        comfy_client.poll_once("prompt_123")


# ---------------------------------------------------------------------------
# poll_until_complete tests
# ---------------------------------------------------------------------------

def test_poll_until_complete_success(comfy_client, mock_httpx_client):
    # First call returns running, second returns complete
    mock_running = MagicMock()
    mock_running.json.return_value = {
        "prompt_123": {"status": {"status_str": "running", "completed": False}, "outputs": {}}
    }
    mock_running.raise_for_status = MagicMock()

    mock_complete = MagicMock()
    mock_complete.json.return_value = {
        "prompt_123": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {"12": {"images": [{"filename": "output.png"}]}},
        }
    }
    mock_complete.raise_for_status = MagicMock()

    mock_httpx_client.get.side_effect = [mock_running, mock_complete]

    with patch("engine.comfy_client.time.sleep") as mock_sleep:
        result = comfy_client.poll_until_complete("prompt_123", poll_interval_s=0.1, max_wait_s=10)

    assert result.state == ComfyJobState.COMPLETE
    assert mock_sleep.call_count == 1


def test_poll_until_complete_oom_raises(comfy_client, mock_httpx_client):
    mock_running = MagicMock()
    mock_running.json.return_value = {
        "prompt_123": {"status": {"status_str": "running", "completed": False}, "outputs": {}}
    }
    mock_running.raise_for_status = MagicMock()

    mock_oom = MagicMock()
    mock_oom.json.return_value = {
        "prompt_123": {
            "status": {"completed": True, "status_str": "error", "error": {"message": "CUDA out of memory"}},
            "outputs": {},
        }
    }
    mock_oom.raise_for_status = MagicMock()

    mock_httpx_client.get.side_effect = [mock_running, mock_oom]

    with patch("engine.comfy_client.time.sleep"):
        with pytest.raises(ComfyOOMError):
            comfy_client.poll_until_complete("prompt_123", poll_interval_s=0.1, max_wait_s=10)


def test_poll_until_complete_timeout(comfy_client, mock_httpx_client):
    mock_running = MagicMock()
    mock_running.json.return_value = {
        "prompt_123": {"status": {"status_str": "running", "completed": False}, "outputs": {}}
    }
    mock_running.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_running

    with patch("engine.comfy_client.time.monotonic", side_effect=[0, 5, 10, 15, 20, 25]):
        with patch("engine.comfy_client.time.sleep"):
            with pytest.raises(TimeoutError):
                comfy_client.poll_until_complete("prompt_123", poll_interval_s=0.1, max_wait_s=10)


# ---------------------------------------------------------------------------
# fetch_output tests
# ---------------------------------------------------------------------------

def test_fetch_output_downloads_files(comfy_client, mock_httpx_client, tmp_path):
    result = ComfyJobResult(
        prompt_id="prompt_123",
        state=ComfyJobState.COMPLETE,
        output_paths=[Path("ep001_scene01_00001.png"), Path("ep001_scene01_00002.png")],
    )

    mock_response1 = MagicMock()
    mock_response1.content = b"fake image data 1"
    mock_response1.raise_for_status = MagicMock()

    mock_response2 = MagicMock()
    mock_response2.content = b"fake image data 2"
    mock_response2.raise_for_status = MagicMock()

    mock_httpx_client.get.side_effect = [mock_response1, mock_response2]

    local_paths = comfy_client.fetch_output(result, tmp_path)

    assert len(local_paths) == 2
    assert local_paths[0].name == "ep001_scene01_00001.png"
    assert local_paths[1].name == "ep001_scene01_00002.png"
    assert local_paths[0].read_bytes() == b"fake image data 1"
    assert local_paths[1].read_bytes() == b"fake image data 2"

    # Verify GET /view called with correct params
    assert mock_httpx_client.get.call_count == 2
    for call in mock_httpx_client.get.call_args_list:
        assert call[0][0] == "http://localhost:8188/view"
        assert "filename" in call[1]["params"]
        assert call[1]["params"]["type"] == "output"


def test_fetch_output_creates_destination_dir(comfy_client, mock_httpx_client, tmp_path):
    result = ComfyJobResult(
        prompt_id="prompt_123",
        state=ComfyJobState.COMPLETE,
        output_paths=[Path("output.png")],
    )

    mock_response = MagicMock()
    mock_response.content = b"data"
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    new_dir = tmp_path / "new" / "nested" / "dir"
    local_paths = comfy_client.fetch_output(result, new_dir)

    assert new_dir.exists()
    assert len(local_paths) == 1


# ---------------------------------------------------------------------------
# queue_depth tests
# ---------------------------------------------------------------------------

def test_queue_depth_returns_total(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "queue_running": [{"id": "1"}, {"id": "2"}],
        "queue_pending": [{"id": "3"}, {"id": "4"}, {"id": "5"}],
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    depth = comfy_client.queue_depth()

    assert depth == 5


def test_queue_depth_empty_queue(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"queue_running": [], "queue_pending": []}
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    depth = comfy_client.queue_depth()

    assert depth == 0


# ---------------------------------------------------------------------------
# is_model_resident tests
# ---------------------------------------------------------------------------

def test_is_model_resident_true(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {
        "models": {"checkpoints": ["sdxl_base_1.0.safetensors", "wan2.2_ti2v_5b.safetensors"]}
    }
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.is_model_resident("sdxl_base_1.0.safetensors")

    assert result is True


def test_is_model_resident_false(comfy_client, mock_httpx_client):
    mock_response = MagicMock()
    mock_response.json.return_value = {"models": {"checkpoints": ["wan2.2_ti2v_5b.safetensors"]}}
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    result = comfy_client.is_model_resident("sdxl_base_1.0.safetensors")

    assert result is False


def test_is_model_resident_error_returns_false(comfy_client, mock_httpx_client):
    mock_httpx_client.get.side_effect = httpx.RequestError("Connection failed")

    result = comfy_client.is_model_resident("sdxl_base_1.0.safetensors")

    assert result is False


# ---------------------------------------------------------------------------
# ComfyJobResult and ComfyOOMError tests
# ---------------------------------------------------------------------------

def test_comfy_job_result_dataclass():
    result = ComfyJobResult(
        prompt_id="test_123",
        state=ComfyJobState.COMPLETE,
        output_paths=[Path("out.png")],
        error_message="test error",
    )
    assert result.prompt_id == "test_123"
    assert result.state == ComfyJobState.COMPLETE
    assert result.output_paths == [Path("out.png")]
    assert result.error_message == "test error"


def test_comfy_oom_error_inheritance():
    assert issubclass(ComfyOOMError, Exception)
    err = ComfyOOMError("Out of memory")
    assert str(err) == "Out of memory"


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

def test_comfy_job_state_enum_values():
    assert ComfyJobState.QUEUED == "queued"
    assert ComfyJobState.RUNNING == "running"
    assert ComfyJobState.COMPLETE == "complete"
    assert ComfyJobState.FAILED == "failed"
    assert ComfyJobState.OOM == "oom"


# ---------------------------------------------------------------------------
# Integration test marker (requires real ComfyUI)
# ---------------------------------------------------------------------------

@pytest.mark.requires_gpu
def test_integration_submit_and_poll():
    """Integration test against a real ComfyUI instance.
    Run with: pytest -m requires_gpu tests/test_comfy_client.py::test_integration_submit_and_poll
    Requires: ComfyUI running at http://localhost:8188 with --lowvram
    """
    client = ComfyUIClient(base_url="http://localhost:8188", timeout_s=600)

    try:
        # Load and mutate a simple workflow
        template = client.load_workflow_template(Path("comfyui/workflows/sdxl_still.json"))
        mutated = client.mutate_still_workflow(
            template,
            positive_prompt="test prompt",
            negative_prompt="ugly, deformed",
            seed=12345,
            ip_adapter_reference_paths=[],
            ip_adapter_weight=0.0,
            filename_prefix="integration_test",
        )

        # Submit
        prompt_id = client.submit(mutated)
        assert prompt_id is not None
        assert len(prompt_id) > 0

        # Poll until complete (or timeout)
        result = client.poll_until_complete(prompt_id, poll_interval_s=5.0, max_wait_s=300.0)

        assert result.state in (ComfyJobState.COMPLETE, ComfyJobState.FAILED)
        if result.state == ComfyJobState.COMPLETE:
            assert len(result.output_paths) > 0
            # Test fetch
            with tempfile.TemporaryDirectory() as tmpdir:
                local_paths = client.fetch_output(result, Path(tmpdir))
                assert len(local_paths) > 0
                assert local_paths[0].exists()

    finally:
        client.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])