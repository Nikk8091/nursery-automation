"""
tests/test_db_schema.py — Unit tests for db/schema.sql and db/session.py

Covers:
- Database initialization from schema.sql
- Row-to-model mapping for all models
- Model-to-params serialization round-trip
- JSON field parsing (visual_signature, localized_metadata, last_qc_critique, etc.)
"""

import json as json_module
import sqlite3
import tempfile
from pathlib import Path

import pytest

from db.models import (
    Asset,
    AssetType,
    BackupLogEntry,
    BatchPhase,
    Character,
    CharacterGenerationControl,
    CharacterMotionProfile,
    CharacterVisualSignature,
    CharacterVoiceProfile,
    CreditTrackerEntry,
    Episode,
    EpisodeStatus,
    Generation,
    GenerationStatus,
    GCLogEntry,
    LocalizedMetadataEntry,
    Project,
    PublishingQueueItem,
    QCResult,
    QCCritique,
    QCDecision,
    RetentionStatus,
    Scene,
)
from db.session import get_connection, init_db, model_to_params, row_to_model


def json_dumps(obj):
    return json_module.dumps(obj)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def temp_db():
    """Create a temporary database file for testing."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = Path(f.name)
    yield db_path
    db_path.unlink(missing_ok=True)


@pytest.fixture
def initialized_db(temp_db):
    """Initialize a database with the schema."""
    init_db(temp_db)
    return temp_db


# ---------------------------------------------------------------------------
# Database initialization tests
# ---------------------------------------------------------------------------

def test_init_db_creates_all_tables(initialized_db):
    """Verify all tables from schema.sql are created."""
    with get_connection(initialized_db) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        tables = [row[0] for row in cursor.fetchall()]

    expected_tables = {
        "projects",
        "characters",
        "episodes",
        "episode_characters",
        "scenes",
        "generations",
        "assets",
        "qc_results",
        "credit_tracker",
        "gc_log",
        "publishing_queue",
        "backup_log",
    }
    assert set(tables) == expected_tables


def test_init_db_creates_indexes(initialized_db):
    """Verify indexes from schema.sql are created."""
    with get_connection(initialized_db) as conn:
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
        indexes = [row[0] for row in cursor.fetchall()]

    expected_indexes = {
        "idx_episodes_project",
        "idx_episodes_hold_from_gc",
        "idx_scenes_episode",
        "idx_generations_scene",
        "idx_generations_status",
        "idx_assets_generation",
        "idx_assets_retention_expiry",
    }
    assert set(indexes) == expected_indexes


def test_init_db_check_constraints(initialized_db):
    """Verify CHECK constraints are enforced."""
    with get_connection(initialized_db) as conn:
        # Test episodes.status constraint
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO episodes (project_id, status) VALUES (1, 'invalid_status')"
            )

        # Test generations.batch_phase constraint
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO generations (scene_id, batch_phase, agent_type, model_used) "
                "VALUES (1, 'INVALID', 'agent', 'model')"
            )

        # Test assets.asset_type constraint
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO assets (generation_id, asset_type, file_path) "
                "VALUES (1, 'invalid_type', '/tmp/test.png')"
            )


# ---------------------------------------------------------------------------
# row_to_model tests for each model
# ---------------------------------------------------------------------------

def test_row_to_model_project(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute(
            "INSERT INTO projects (name, channel_id) VALUES (?, ?)",
            ("Test Project", "UC123"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM projects WHERE name = ?", ("Test Project",)).fetchone()

    project = row_to_model(row, Project)
    assert project.project_id == 1
    assert project.name == "Test Project"
    assert project.channel_id == "UC123"
    assert project.created_at is not None


def test_row_to_model_character(initialized_db):
    visual_sig = {
        "species_archetype": "animal",
        "color_palette": ["#FF0000", "#00FF00"],
        "silhouette_notes": "round",
        "costume": "bow tie",
        "art_style_tag": "watercolor",
    }
    gen_control = {
        "base_seed": 12345,
        "seed_lock": True,
        "reference_image_ids": ["img1", "img2"],
        "controlnet_type": "openpose",
        "ip_adapter_weight": 0.5,
        "negative_prompt_lock": "ugly, deformed",
        "comfyui_workflow_template": "sdxl_still.json",
        "thumbnail_workflow_template": "thumbnail_still.json",
    }
    voice_profile = {
        "engine": "ace-step-1.5",
        "vocal_style_tag": "cheerful",
        "narration_engine": "kokoro-82m",
        "narration_voice_id": "af_heart",
    }
    motion_profile = {
        "assembly_mode": "beat_driven_bob",
        "beat_bob_intensity": 0.7,
        "sync_reference": "dynamic_onset_grid_isolated_stem",
    }

    with get_connection(initialized_db) as conn:
        conn.execute(
            """INSERT INTO characters (character_id, name, visual_signature, generation_control,
                           voice_profile, motion_profile)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (
                "char_001",
                "Test Char",
                json_dumps(visual_sig),
                json_dumps(gen_control),
                json_dumps(voice_profile),
                json_dumps(motion_profile),
            ),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM characters WHERE character_id = ?", ("char_001",)).fetchone()

    char = row_to_model(row, Character)
    assert char.character_id == "char_001"
    assert char.name == "Test Char"
    assert char.visual_signature.species_archetype == "animal"
    assert char.visual_signature.color_palette == ["#FF0000", "#00FF00"]
    assert char.generation_control.base_seed == 12345
    assert char.generation_control.ip_adapter_weight == 0.5
    assert char.voice_profile.engine == "ace-step-1.5"
    assert char.motion_profile.beat_bob_intensity == 0.7


def test_row_to_model_episode_with_localized_metadata(initialized_db):
    localized_meta = {
        "en": {"title": "English Title", "description": "English Description"},
        "es": {"title": "Título Español", "description": "Descripción Español"},
    }

    with get_connection(initialized_db) as conn:
        # Need a project first
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute(
            """INSERT INTO episodes (project_id, title, status, rhyme_theme, hold_from_gc, localized_metadata)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (1, "Test Episode", "draft", "animals", 1, json_dumps(localized_meta)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM episodes WHERE title = ?", ("Test Episode",)).fetchone()

    episode = row_to_model(row, Episode)
    assert episode.episode_id == 1
    assert episode.project_id == 1
    assert episode.title == "Test Episode"
    assert episode.status == EpisodeStatus.DRAFT
    assert episode.rhyme_theme == "animals"
    assert episode.hold_from_gc is True
    assert episode.localized_metadata is not None
    assert "en" in episode.localized_metadata
    assert "es" in episode.localized_metadata
    assert episode.localized_metadata["en"].title == "English Title"
    assert episode.localized_metadata["es"].title == "Título Español"


def test_row_to_model_episode_without_localized_metadata(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute(
            "INSERT INTO episodes (project_id, title) VALUES (?, ?)",
            (1, "Test Episode"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM episodes WHERE title = ?", ("Test Episode",)).fetchone()

    episode = row_to_model(row, Episode)
    assert episode.localized_metadata is None
    assert episode.hold_from_gc is False
    assert episode.status == EpisodeStatus.DRAFT


def test_row_to_model_scene(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute(
            "INSERT INTO scenes (episode_id, sequence_order, shot_description, status) VALUES (?, ?, ?, ?)",
            (1, 1, "A beautiful sunrise", "approved"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM scenes WHERE episode_id = ?", (1,)).fetchone()

    scene = row_to_model(row, Scene)
    assert scene.scene_id == 1
    assert scene.episode_id == 1
    assert scene.sequence_order == 1
    assert scene.shot_description == "A beautiful sunrise"
    assert scene.status == "approved"


def test_row_to_model_generation_with_qc_critique(initialized_db):
    critique = {
        "generation_id": 1,
        "failure_reason": "color mismatch",
        "detail": "Character shirt color does not match reference",
        "corrective_negative_prompt_append": "wrong shirt color",
        "attempt": 1,
    }

    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        conn.execute(
            """INSERT INTO generations (scene_id, batch_phase, agent_type, model_used,
                           comfyui_prompt_id, status, retry_count, batch_size_used, last_qc_critique)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, "C", "visual_prompt_agent", "sdxl", "prompt_123", "running", 1, 4, json_dumps(critique)),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM generations WHERE comfyui_prompt_id = ?", ("prompt_123",)).fetchone()

    gen = row_to_model(row, Generation)
    assert gen.generation_id == 1
    assert gen.scene_id == 1
    assert gen.batch_phase == BatchPhase.C
    assert gen.agent_type == "visual_prompt_agent"
    assert gen.model_used == "sdxl"
    assert gen.comfyui_prompt_id == "prompt_123"
    assert gen.status == GenerationStatus.RUNNING
    assert gen.retry_count == 1
    assert gen.batch_size_used == 4
    assert gen.last_qc_critique is not None
    assert gen.last_qc_critique.failure_reason == "color mismatch"
    assert gen.last_qc_critique.attempt == 1


def test_row_to_model_generation_without_qc_critique(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        conn.execute(
            "INSERT INTO generations (scene_id, batch_phase, agent_type, model_used) VALUES (?, ?, ?, ?)",
            (1, "A1", "creative_director", "llama3"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM generations WHERE batch_phase = ?", ("A1",)).fetchone()

    gen = row_to_model(row, Generation)
    assert gen.batch_phase == BatchPhase.A1
    assert gen.status == GenerationStatus.QUEUED
    assert gen.last_qc_critique is None


def test_row_to_model_asset(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        conn.execute(
            "INSERT INTO generations (scene_id, batch_phase, agent_type, model_used) VALUES (?, ?, ?, ?)",
            (1, "C", "visual_prompt_agent", "sdxl"),
        )
        conn.execute(
            """INSERT INTO assets (generation_id, asset_type, file_path, qc_similarity_score,
                           retention_status, expires_at)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (1, "still", "/output/still_001.png", 0.95, "persistent", "2026-08-25T12:00:00"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM assets WHERE file_path = ?", ("/output/still_001.png",)).fetchone()

    asset = row_to_model(row, Asset)
    assert asset.asset_id == 1
    assert asset.generation_id == 1
    assert asset.asset_type == AssetType.STILL
    assert asset.file_path == "/output/still_001.png"
    assert asset.qc_similarity_score == 0.95
    assert asset.retention_status == RetentionStatus.PERSISTENT
    assert asset.expires_at is not None


def test_row_to_model_qc_result(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute(
            """INSERT INTO qc_results (episode_id, visual_quality_score, consistency_score,
                           beat_sync_score, appropriateness_score, repetition_score, total_score, decision)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (1, 85, 90, 80, 95, 70, 84, "publish_ready"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM qc_results WHERE episode_id = ?", (1,)).fetchone()

    qc = row_to_model(row, QCResult)
    assert qc.qc_id == 1
    assert qc.episode_id == 1
    assert qc.visual_quality_score == 85
    assert qc.consistency_score == 90
    assert qc.beat_sync_score == 80
    assert qc.appropriateness_score == 95
    assert qc.repetition_score == 70
    assert qc.total_score == 84
    assert qc.decision == QCDecision.PUBLISH_READY


def test_row_to_model_credit_tracker_entry(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        conn.execute(
            "INSERT INTO generations (scene_id, batch_phase, agent_type, model_used) VALUES (?, ?, ?, ?)",
            (1, "C", "visual_prompt_agent", "sdxl"),
        )
        conn.execute(
            "INSERT INTO credit_tracker (generation_id, service, units_consumed) VALUES (?, ?, ?)",
            (1, "comfyui", 10),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM credit_tracker WHERE service = ?", ("comfyui",)).fetchone()

    credit = row_to_model(row, CreditTrackerEntry)
    assert credit.log_id == 1
    assert credit.generation_id == 1
    assert credit.service == "comfyui"
    assert credit.units_consumed == 10
    assert credit.logged_at is not None


def test_row_to_model_gc_log_entry(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        conn.execute(
            "INSERT INTO generations (scene_id, batch_phase, agent_type, model_used) VALUES (?, ?, ?, ?)",
            (1, "C", "visual_prompt_agent", "sdxl"),
        )
        conn.execute(
            "INSERT INTO assets (generation_id, asset_type, file_path) VALUES (?, ?, ?)",
            (1, "still", "/tmp/test.png"),
        )
        conn.execute(
            "INSERT INTO gc_log (asset_id, action) VALUES (?, ?)",
            (1, "soft_delete"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM gc_log WHERE asset_id = ?", (1,)).fetchone()

    gc = row_to_model(row, GCLogEntry)
    assert gc.gc_id == 1
    assert gc.asset_id == 1
    assert gc.action == "soft_delete"
    assert gc.executed_at is not None


def test_row_to_model_publishing_queue_item(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute(
            "INSERT INTO publishing_queue (episode_id, upload_status, youtube_video_id, scheduled_at) "
            "VALUES (?, ?, ?, ?)",
            (1, "uploaded", "dQw4w9WgXcQ", "2026-08-25T12:00:00"),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM publishing_queue WHERE episode_id = ?", (1,)).fetchone()

    pqi = row_to_model(row, PublishingQueueItem)
    assert pqi.queue_id == 1
    assert pqi.episode_id == 1
    assert pqi.upload_status == "uploaded"
    assert pqi.youtube_video_id == "dQw4w9WgXcQ"
    assert pqi.scheduled_at is not None


def test_row_to_model_backup_log_entry(initialized_db):
    with get_connection(initialized_db) as conn:
        conn.execute(
            "INSERT INTO backup_log (source_path, remote_path, status, bytes_transferred) "
            "VALUES (?, ?, ?, ?)",
            ("characters/", "gdrive:backup/characters/", "success", 1024000),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM backup_log WHERE source_path = ?", ("characters/",)).fetchone()

    backup = row_to_model(row, BackupLogEntry)
    assert backup.backup_id == 1
    assert backup.source_path == "characters/"
    assert backup.remote_path == "gdrive:backup/characters/"
    assert backup.status == "success"
    assert backup.bytes_transferred == 1024000
    assert backup.executed_at is not None


# ---------------------------------------------------------------------------
# model_to_params tests (serialization round-trip)
# ---------------------------------------------------------------------------

def test_model_to_params_project():
    project = Project(project_id=1, name="Test Project", channel_id="UC123")
    params = model_to_params(project)
    assert params["project_id"] == 1
    assert params["name"] == "Test Project"
    assert params["channel_id"] == "UC123"


def test_model_to_params_character():
    char = Character(
        character_id="char_001",
        name="Test Char",
        visual_signature=CharacterVisualSignature(
            species_archetype="animal",
            color_palette=["#FF0000"],
            silhouette_notes="round",
            costume="bow tie",
            art_style_tag="watercolor",
        ),
        generation_control=CharacterGenerationControl(
            base_seed=12345,
            seed_lock=True,
            reference_image_ids=["img1"],
            controlnet_type="openpose",
            ip_adapter_weight=0.5,
            negative_prompt_lock="ugly",
            comfyui_workflow_template="sdxl_still.json",
            thumbnail_workflow_template="thumbnail_still.json",
        ),
        voice_profile=CharacterVoiceProfile(
            vocal_style_tag="cheerful",
            narration_voice_id="af_heart",
        ),
        motion_profile=CharacterMotionProfile(beat_bob_intensity=0.7),
    )
    params = model_to_params(char)
    assert params["character_id"] == "char_001"
    assert params["name"] == "Test Char"
    assert "visual_signature" in params
    assert "generation_control" in params
    assert "voice_profile" in params
    assert "motion_profile" in params

    # Verify JSON serialization
    import json
    vs = json.loads(params["visual_signature"])
    assert vs["species_archetype"] == "animal"
    gc = json.loads(params["generation_control"])
    assert gc["base_seed"] == 12345


def test_model_to_params_episode_with_localized_metadata():
    episode = Episode(
        episode_id=1,
        project_id=1,
        title="Test Episode",
        status=EpisodeStatus.IN_PROGRESS,
        rhyme_theme="animals",
        hold_from_gc=True,
        localized_metadata={
            "en": LocalizedMetadataEntry(title="English", description="Desc"),
            "es": LocalizedMetadataEntry(title="Español", description="Descripción"),
        },
    )
    params = model_to_params(episode)
    assert params["episode_id"] == 1
    assert params["status"] == "in_progress"
    assert params["hold_from_gc"] == 1
    assert params["localized_metadata"] is not None

    import json
    lm = json.loads(params["localized_metadata"])
    assert lm["en"]["title"] == "English"
    assert lm["es"]["title"] == "Español"


def test_model_to_params_episode_without_localized_metadata():
    episode = Episode(project_id=1, title="Test Episode")
    params = model_to_params(episode)
    assert params["localized_metadata"] is None
    assert params["hold_from_gc"] == 0
    assert params["status"] == "draft"


def test_model_to_params_generation_with_qc_critique():
    critique = QCCritique(
        generation_id=1,
        failure_reason="color mismatch",
        detail="Wrong color",
        corrective_negative_prompt_append="wrong color",
        attempt=1,
    )
    gen = Generation(
        generation_id=1,
        scene_id=1,
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
        status=GenerationStatus.RUNNING,
        retry_count=1,
        batch_size_used=4,
        last_qc_critique=critique,
    )
    params = model_to_params(gen)
    assert params["batch_phase"] == "C"
    assert params["status"] == "running"
    assert params["batch_size_used"] == 4
    assert params["last_qc_critique"] is not None

    import json
    lqc = json.loads(params["last_qc_critique"])
    assert lqc["failure_reason"] == "color mismatch"
    assert lqc["attempt"] == 1


def test_model_to_params_generation_without_qc_critique():
    gen = Generation(
        batch_phase=BatchPhase.A1,
        agent_type="creative_director",
        model_used="llama3",
    )
    params = model_to_params(gen)
    assert params["batch_phase"] == "A1"
    assert params["status"] == "queued"
    assert params["last_qc_critique"] is None


def test_model_to_params_asset():
    asset = Asset(
        asset_id=1,
        generation_id=1,
        asset_type=AssetType.STILL,
        file_path="/tmp/test.png",
        qc_similarity_score=0.95,
        retention_status=RetentionStatus.PERSISTENT,
    )
    params = model_to_params(asset)
    assert params["asset_type"] == "still"
    assert params["retention_status"] == "persistent"


def test_model_to_params_qc_result():
    qc = QCResult(
        qc_id=1,
        episode_id=1,
        visual_quality_score=85,
        consistency_score=90,
        beat_sync_score=80,
        appropriateness_score=95,
        repetition_score=70,
        total_score=84,
        decision=QCDecision.PUBLISH_READY,
    )
    params = model_to_params(qc)
    assert params["decision"] == "publish_ready"


# ---------------------------------------------------------------------------
# Full round-trip tests (insert -> select -> row_to_model -> model_to_params -> update)
# ---------------------------------------------------------------------------

def test_full_roundtrip_episode(initialized_db):
    """Test insert -> select -> model -> params -> update cycle."""
    episode = Episode(
        project_id=1,
        title="Roundtrip Test",
        status=EpisodeStatus.IN_PROGRESS,
        rhyme_theme="nature",
        hold_from_gc=True,
        localized_metadata={
            "en": LocalizedMetadataEntry(title="Nature Song", description="A song about nature"),
        },
    )

    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        params = model_to_params(episode)
        # Remove episode_id and created_at for insert (let DEFAULT handle created_at)
        insert_params = {k: v for k, v in params.items() if k not in ("episode_id", "created_at")}
        placeholders = ", ".join(["?"] * len(insert_params))
        columns = ", ".join(insert_params.keys())
        conn.execute(
            f"INSERT INTO episodes ({columns}) VALUES ({placeholders})",
            tuple(insert_params.values()),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM episodes WHERE title = ?", ("Roundtrip Test",)).fetchone()

    loaded = row_to_model(row, Episode)
    assert loaded.title == "Roundtrip Test"
    assert loaded.status == EpisodeStatus.IN_PROGRESS
    assert loaded.hold_from_gc is True
    assert loaded.localized_metadata is not None
    assert loaded.localized_metadata["en"].title == "Nature Song"

    # Serialize back
    back_params = model_to_params(loaded)
    assert back_params["status"] == "in_progress"
    assert back_params["hold_from_gc"] == 1


def test_full_roundtrip_generation(initialized_db):
    """Test insert -> select -> model -> params cycle for Generation."""
    gen = Generation(
        scene_id=1,
        batch_phase=BatchPhase.C,
        agent_type="visual_prompt_agent",
        model_used="sdxl",
        status=GenerationStatus.COMPLETE,
        retry_count=0,
        batch_size_used=4,
        last_qc_critique=QCCritique(
            generation_id=1,
            failure_reason="fixed",
            detail="was fixed",
            corrective_negative_prompt_append="fixed",
            attempt=1,
        ),
    )

    with get_connection(initialized_db) as conn:
        conn.execute("INSERT INTO projects (name) VALUES (?)", ("Test Project",))
        conn.execute("INSERT INTO episodes (project_id, title) VALUES (?, ?)", (1, "Test"))
        conn.execute("INSERT INTO scenes (episode_id, sequence_order) VALUES (?, ?)", (1, 1))
        params = model_to_params(gen)
        insert_params = {k: v for k, v in params.items() if k not in ("generation_id", "created_at")}
        placeholders = ", ".join(["?"] * len(insert_params))
        columns = ", ".join(insert_params.keys())
        conn.execute(
            f"INSERT INTO generations ({columns}) VALUES ({placeholders})",
            tuple(insert_params.values()),
        )
        conn.commit()

        row = conn.execute("SELECT * FROM generations WHERE batch_phase = ?", ("C",)).fetchone()

    loaded = row_to_model(row, Generation)
    assert loaded.batch_phase == BatchPhase.C
    assert loaded.status == GenerationStatus.COMPLETE
    assert loaded.batch_size_used == 4
    assert loaded.last_qc_critique is not None
    assert loaded.last_qc_critique.failure_reason == "fixed"

    back_params = model_to_params(loaded)
    assert back_params["status"] == "complete"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])