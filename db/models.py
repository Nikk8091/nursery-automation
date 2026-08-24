"""
db/models.py — Canonical Pydantic v2 data contracts for the Nursery Rhyme Video Factory.

Source of truth: docs/specs/spec_02_database_schema.md §3.
ALL data crossing an agent<->agent or agent<->database boundary MUST use these
models (see .cursorrules / CLAUDE.md / .windsurfrules / system_prompt.md, rule 3).

This file is intentionally complete (not a stub) — Pydantic models are pure
data contracts, so the "interface" and "implementation" are the same thing.
"""

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class EpisodeStatus(str, Enum):
    DRAFT = "draft"
    IN_PROGRESS = "in_progress"
    HUMAN_REVIEW = "human_review"
    PUBLISH_READY = "publish_ready"
    PUBLISHED = "published"
    FAILED = "failed"


class BatchPhase(str, Enum):
    A1 = "A1"
    A2 = "A2"
    B = "B"
    C = "C"
    D = "D"


class GenerationStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETE = "complete"
    FAILED = "failed"
    OOM = "oom"
    MANUAL_FLAG = "manual_flag"


class AssetType(str, Enum):
    STILL = "still"
    THUMBNAIL = "thumbnail"
    CLIP = "clip"
    AUDIO_SONG = "audio_song"
    AUDIO_NARRATION = "audio_narration"
    FINAL_RENDER = "final_render"


class RetentionStatus(str, Enum):
    EPHEMERAL = "ephemeral"
    PERSISTENT = "persistent"


class QCDecision(str, Enum):
    PUBLISH_READY = "publish_ready"
    HUMAN_REVIEW = "human_review"
    REGENERATE = "regenerate"


# ---------------------------------------------------------------------------
# Core records
# ---------------------------------------------------------------------------

class Project(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    project_id: Optional[int] = None
    name: str
    channel_id: Optional[str] = None
    created_at: Optional[datetime] = None


class CharacterVisualSignature(BaseModel):
    species_archetype: str
    color_palette: list[str]
    silhouette_notes: str
    costume: str
    art_style_tag: str


class CharacterGenerationControl(BaseModel):
    base_seed: int
    seed_lock: bool = True
    reference_image_ids: list[str]
    controlnet_type: str
    ip_adapter_weight: float = Field(ge=0.0, le=1.0)
    negative_prompt_lock: str
    comfyui_workflow_template: str
    thumbnail_workflow_template: str


class CharacterVoiceProfile(BaseModel):
    engine: str = "ace-step-1.5"
    vocal_style_tag: str
    narration_engine: str = "kokoro-82m"
    narration_voice_id: str


class CharacterMotionProfile(BaseModel):
    assembly_mode: str = "beat_driven_bob"
    beat_bob_intensity: float = Field(ge=0.0, le=1.0)
    sync_reference: str = "dynamic_onset_grid_isolated_stem"
    note: Optional[str] = None


class Character(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    character_id: str
    name: str
    visual_signature: CharacterVisualSignature
    generation_control: CharacterGenerationControl
    voice_profile: CharacterVoiceProfile
    motion_profile: CharacterMotionProfile


class LocalizedMetadataEntry(BaseModel):
    title: str
    description: str


class Episode(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    episode_id: Optional[int] = None
    project_id: int
    title: Optional[str] = None
    status: EpisodeStatus = EpisodeStatus.DRAFT
    rhyme_theme: Optional[str] = None
    thumbnail_asset_id: Optional[int] = None
    hold_from_gc: bool = False
    localized_metadata: Optional[dict[str, LocalizedMetadataEntry]] = None
    created_at: Optional[datetime] = None


class Scene(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    scene_id: Optional[int] = None
    episode_id: int
    sequence_order: int
    shot_description: Optional[str] = None
    status: str = "pending"  # 'pending' | 'approved' | 'rejected'


class QCCritique(BaseModel):
    """Structured failure reason passed from QCAgent back to VisualPromptAgent
    to drive corrective-prompt retries. See spec_03 §4.1."""
    generation_id: int
    failure_reason: str
    detail: str
    corrective_negative_prompt_append: str
    attempt: int


class Generation(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    generation_id: Optional[int] = None
    scene_id: Optional[int] = None
    batch_phase: BatchPhase
    agent_type: str
    model_used: str
    comfyui_prompt_id: Optional[str] = None
    status: GenerationStatus = GenerationStatus.QUEUED
    retry_count: int = 0
    batch_size_used: Optional[int] = None
    last_qc_critique: Optional[QCCritique] = None
    created_at: Optional[datetime] = None


class Asset(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    asset_id: Optional[int] = None
    generation_id: int
    asset_type: AssetType
    file_path: str
    qc_similarity_score: Optional[float] = None
    retention_status: RetentionStatus = RetentionStatus.EPHEMERAL
    expires_at: Optional[datetime] = None
    deleted_at: Optional[datetime] = None


class QCResult(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    qc_id: Optional[int] = None
    episode_id: int
    visual_quality_score: int = Field(ge=0, le=100)
    consistency_score: int = Field(ge=0, le=100)
    beat_sync_score: int = Field(ge=0, le=100)
    appropriateness_score: int = Field(ge=0, le=100)
    repetition_score: int = Field(ge=0, le=100)
    total_score: int = Field(ge=0, le=100)
    decision: QCDecision


class CreditTrackerEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    log_id: Optional[int] = None
    generation_id: Optional[int] = None
    service: str
    units_consumed: int
    logged_at: Optional[datetime] = None


class GCLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    gc_id: Optional[int] = None
    asset_id: int
    action: str  # 'soft_delete' | 'hard_delete' | 'skipped_hold'
    executed_at: Optional[datetime] = None


class PublishingQueueItem(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    queue_id: Optional[int] = None
    episode_id: int
    upload_status: str = "pending"  # 'pending' | 'uploaded' | 'failed'
    youtube_video_id: Optional[str] = None
    scheduled_at: Optional[datetime] = None


class BackupLogEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    backup_id: Optional[int] = None
    source_path: str
    remote_path: str
    status: str  # 'success' | 'failed'
    bytes_transferred: Optional[int] = None
    executed_at: Optional[datetime] = None
