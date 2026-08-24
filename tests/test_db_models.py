"""
tests/test_db_models.py — Unit tests for db/models.py

Covers:
- Enum round-tripping (str<->Enum)
- Episode.localized_metadata nested-model validation
- Generation.last_qc_critique optionality
"""

import pytest
from datetime import datetime

from db.models import (
    EpisodeStatus,
    BatchPhase,
    GenerationStatus,
    AssetType,
    RetentionStatus,
    QCDecision,
    Project,
    CharacterVisualSignature,
    CharacterGenerationControl,
    CharacterVoiceProfile,
    CharacterMotionProfile,
    Character,
    LocalizedMetadataEntry,
    Episode,
    Scene,
    QCCritique,
    Generation,
    Asset,
    QCResult,
    CreditTrackerEntry,
    GCLogEntry,
    PublishingQueueItem,
    BackupLogEntry,
)


# ---------------------------------------------------------------------------
# Enum round-tripping tests
# ---------------------------------------------------------------------------

def test_episode_status_roundtrip():
    for member in EpisodeStatus:
        assert EpisodeStatus(member.value) == member
        assert member == member.value


def test_batch_phase_roundtrip():
    for member in BatchPhase:
        assert BatchPhase(member.value) == member
        assert member == member.value


def test_generation_status_roundtrip():
    for member in GenerationStatus:
        assert GenerationStatus(member.value) == member
        assert member == member.value


def test_asset_type_roundtrip():
    for member in AssetType:
        assert AssetType(member.value) == member
        assert member == member.value


def test_retention_status_roundtrip():
    for member in RetentionStatus:
        assert RetentionStatus(member.value) == member
        assert member == member.value


def test_qc_decision_roundtrip():
    for member in QCDecision:
        assert QCDecision(member.value) == member
        assert member == member.value


def test_enum_string_comparison():
    assert EpisodeStatus.DRAFT == "draft"
    assert BatchPhase.A1 == "A1"
    assert GenerationStatus.QUEUED == "queued"
    assert AssetType.STILL == "still"
    assert RetentionStatus.EPHEMERAL == "ephemeral"
    assert QCDecision.PUBLISH_READY == "publish_ready"


# ---------------------------------------------------------------------------
# Episode.localized_metadata nested-model validation
# ---------------------------------------------------------------------------

def test_localized_metadata_entry_valid():
    entry = LocalizedMetadataEntry(title="Test Title", description="Test Description")
    assert entry.title == "Test Title"
    assert entry.description == "Test Description"


def test_localized_metadata_entry_missing_fields():
    with pytest.raises(ValueError):
        LocalizedMetadataEntry(title="Only Title")


def test_episode_localized_metadata_dict_of_models():
    episode = Episode(
        project_id=1,
        title="Test Episode",
        localized_metadata={
            "en": LocalizedMetadataEntry(title="English Title", description="English Desc"),
            "es": LocalizedMetadataEntry(title="Título Español", description="Descripción Español"),
        },
    )
    assert episode.localized_metadata is not None
    assert "en" in episode.localized_metadata
    assert "es" in episode.localized_metadata
    assert isinstance(episode.localized_metadata["en"], LocalizedMetadataEntry)
    assert episode.localized_metadata["en"].title == "English Title"
    assert episode.localized_metadata["es"].title == "Título Español"


def test_episode_localized_metadata_none():
    episode = Episode(project_id=1, title="Test Episode", localized_metadata=None)
    assert episode.localized_metadata is None


def test_episode_localized_metadata_missing():
    episode = Episode(project_id=1, title="Test Episode")
    assert episode.localized_metadata is None


def test_episode_localized_metadata_invalid_value_type():
    with pytest.raises(ValueError):
        Episode(project_id=1, title="Test", localized_metadata={"en": "not a model"})


def test_episode_localized_metadata_empty_dict():
    episode = Episode(project_id=1, title="Test Episode", localized_metadata={})
    assert episode.localized_metadata == {}


# ---------------------------------------------------------------------------
# Generation.last_qc_critique optionality
# ---------------------------------------------------------------------------

def test_generation_last_qc_critique_none_by_default():
    gen = Generation(
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
    )
    assert gen.last_qc_critique is None


def test_generation_last_qc_critique_set():
    critique = QCCritique(
        generation_id=1,
        failure_reason="color mismatch",
        detail="Character shirt color does not match reference",
        corrective_negative_prompt_append="wrong shirt color",
        attempt=1,
    )
    gen = Generation(
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
        last_qc_critique=critique,
    )
    assert gen.last_qc_critique is not None
    assert gen.last_qc_critique.failure_reason == "color mismatch"
    assert gen.last_qc_critique.attempt == 1


def test_generation_last_qc_critique_can_be_set_after_creation():
    gen = Generation(
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
    )
    assert gen.last_qc_critique is None

    critique = QCCritique(
        generation_id=gen.generation_id or 1,
        failure_reason="anatomy error",
        detail="Extra fingers detected",
        corrective_negative_prompt_append="extra fingers, malformed hands",
        attempt=2,
    )
    gen.last_qc_critique = critique
    assert gen.last_qc_critique is not None
    assert gen.last_qc_critique.failure_reason == "anatomy error"
    assert gen.last_qc_critique.attempt == 2


# ---------------------------------------------------------------------------
# Additional model validation tests
# ---------------------------------------------------------------------------

def test_character_visual_signature_validation():
    vs = CharacterVisualSignature(
        species_archetype="animal",
        color_palette=["#FF0000", "#00FF00"],
        silhouette_notes="round",
        costume="bow tie",
        art_style_tag="watercolor",
    )
    assert vs.species_archetype == "animal"
    assert len(vs.color_palette) == 2


def test_character_generation_control_ip_adapter_weight_bounds():
    gc = CharacterGenerationControl(
        base_seed=12345,
        seed_lock=True,
        reference_image_ids=["img1", "img2"],
        controlnet_type="openpose",
        ip_adapter_weight=0.5,
        negative_prompt_lock="ugly, deformed",
        comfyui_workflow_template="sdxl_still.json",
        thumbnail_workflow_template="thumbnail_still.json",
    )
    assert 0.0 <= gc.ip_adapter_weight <= 1.0

    with pytest.raises(ValueError):
        CharacterGenerationControl(
            base_seed=12345,
            seed_lock=True,
            reference_image_ids=[],
            controlnet_type="openpose",
            ip_adapter_weight=1.5,
            negative_prompt_lock="ugly",
            comfyui_workflow_template="sdxl_still.json",
            thumbnail_workflow_template="thumbnail_still.json",
        )

    with pytest.raises(ValueError):
        CharacterGenerationControl(
            base_seed=12345,
            seed_lock=True,
            reference_image_ids=[],
            controlnet_type="openpose",
            ip_adapter_weight=-0.1,
            negative_prompt_lock="ugly",
            comfyui_workflow_template="sdxl_still.json",
            thumbnail_workflow_template="thumbnail_still.json",
        )


def test_character_motion_profile_beat_bob_intensity_bounds():
    mp = CharacterMotionProfile(
        assembly_mode="beat_driven_bob",
        beat_bob_intensity=0.7,
        sync_reference="dynamic_onset_grid_isolated_stem",
    )
    assert 0.0 <= mp.beat_bob_intensity <= 1.0

    with pytest.raises(ValueError):
        CharacterMotionProfile(
            assembly_mode="beat_driven_bob",
            beat_bob_intensity=1.5,
            sync_reference="dynamic_onset_grid_isolated_stem",
        )


def test_qc_result_scores_bounds():
    qc = QCResult(
        episode_id=1,
        visual_quality_score=85,
        consistency_score=90,
        beat_sync_score=80,
        appropriateness_score=95,
        repetition_score=70,
        total_score=84,
        decision=QCDecision.PUBLISH_READY,
    )
    assert all(0 <= getattr(qc, f) <= 100 for f in [
        "visual_quality_score", "consistency_score", "beat_sync_score",
        "appropriateness_score", "repetition_score", "total_score"
    ])

    with pytest.raises(ValueError):
        QCResult(
            episode_id=1,
            visual_quality_score=101,
            consistency_score=90,
            beat_sync_score=80,
            appropriateness_score=95,
            repetition_score=70,
            total_score=84,
            decision=QCDecision.PUBLISH_READY,
        )


def test_project_defaults():
    p = Project(name="Test Project")
    assert p.project_id is None
    assert p.channel_id is None
    assert p.created_at is None


def test_character_defaults():
    c = Character(
        character_id="char_001",
        name="Test Char",
        visual_signature=CharacterVisualSignature(
            species_archetype="animal",
            color_palette=["#FFF"],
            silhouette_notes="round",
            costume="none",
            art_style_tag="flat",
        ),
        generation_control=CharacterGenerationControl(
            base_seed=42,
            seed_lock=True,
            reference_image_ids=[],
            controlnet_type="canny",
            ip_adapter_weight=0.8,
            negative_prompt_lock="bad",
            comfyui_workflow_template="sdxl_still.json",
            thumbnail_workflow_template="thumbnail_still.json",
        ),
        voice_profile=CharacterVoiceProfile(
            engine="ace-step-1.5",
            vocal_style_tag="cheerful",
            narration_engine="kokoro-82m",
            narration_voice_id="af_heart",
        ),
        motion_profile=CharacterMotionProfile(beat_bob_intensity=0.5),
    )
    assert c.motion_profile.assembly_mode == "beat_driven_bob"
    assert c.motion_profile.beat_bob_intensity == 0.5
    assert c.voice_profile.engine == "ace-step-1.5"
    assert c.voice_profile.narration_engine == "kokoro-82m"


def test_asset_defaults():
    a = Asset(
        generation_id=1,
        asset_type=AssetType.STILL,
        file_path="/tmp/test.png",
    )
    assert a.retention_status == RetentionStatus.EPHEMERAL
    assert a.qc_similarity_score is None
    assert a.expires_at is None
    assert a.deleted_at is None


def test_episode_defaults():
    e = Episode(project_id=1, title="Test")
    assert e.status == EpisodeStatus.DRAFT
    assert e.hold_from_gc is False
    assert e.thumbnail_asset_id is None
    assert e.rhyme_theme is None


def test_scene_defaults():
    s = Scene(episode_id=1, sequence_order=1)
    assert s.status == "pending"
    assert s.shot_description is None


def test_generation_defaults():
    g = Generation(
        batch_phase=BatchPhase.C,
        agent_type="test_agent",
        model_used="test_model",
    )
    assert g.status == GenerationStatus.QUEUED
    assert g.retry_count == 0
    assert g.batch_size_used is None
    assert g.comfyui_prompt_id is None
    assert g.created_at is None


def test_publishing_queue_item_defaults():
    pqi = PublishingQueueItem(episode_id=1)
    assert pqi.upload_status == "pending"
    assert pqi.youtube_video_id is None
    assert pqi.scheduled_at is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])