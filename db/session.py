"""
db/session.py — SQLite connection/session helpers.

Source of truth: docs/specs/spec_02_database_schema.md.
"""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Type, TypeVar

from pydantic import BaseModel

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

ModelT = TypeVar("ModelT", bound=BaseModel)

DEFAULT_DB_PATH = Path("db/nursery_factory.db")


def init_db(db_path: Path = DEFAULT_DB_PATH, schema_path: Path = Path("db/schema.sql")) -> None:
    """Apply db/schema.sql to a fresh SQLite file if it doesn't already exist."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    schema_text = schema_path.read_text(encoding="utf-8")
    with sqlite3.connect(db_path) as conn:
        conn.executescript(schema_text)


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with row_factory set for dict-like
    row access (required by row_to_model below)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _parse_json_field(value: str | None, model_class: Type[BaseModel] | None = None) -> Any:
    """Parse a JSON string field, optionally validating against a Pydantic model."""
    if value is None:
        return None
    data = json.loads(value)
    if model_class is not None:
        return model_class.model_validate(data)
    return data


def _parse_json_dict_field(
    value: str | None, value_model: Type[BaseModel]
) -> dict[str, BaseModel] | None:
    """Parse a JSON string representing a dict of models."""
    if value is None:
        return None
    raw_dict = json.loads(value)
    return {k: value_model.model_validate(v) for k, v in raw_dict.items()}


def row_to_model(row: sqlite3.Row, model: Type[ModelT]) -> ModelT:
    """Map a raw sqlite3.Row to the given Pydantic model from db/models.py,
    parsing any JSON-encoded columns before validation."""
    data = dict(row)

    if model is Project:
        if data.get("created_at"):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        return Project.model_validate(data)

    if model is Character:
        data["visual_signature"] = _parse_json_field(data.get("visual_signature"), CharacterVisualSignature)
        data["generation_control"] = _parse_json_field(data.get("generation_control"), CharacterGenerationControl)
        data["voice_profile"] = _parse_json_field(data.get("voice_profile"), CharacterVoiceProfile)
        data["motion_profile"] = _parse_json_field(data.get("motion_profile"), CharacterMotionProfile)
        return Character.model_validate(data)

    if model is Episode:
        if data.get("created_at"):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("status"):
            data["status"] = EpisodeStatus(data["status"])
        data["hold_from_gc"] = bool(data.get("hold_from_gc", 0))
        data["localized_metadata"] = _parse_json_dict_field(data.get("localized_metadata"), LocalizedMetadataEntry)
        return Episode.model_validate(data)

    if model is Scene:
        return Scene.model_validate(data)

    if model is Generation:
        if data.get("created_at"):
            data["created_at"] = datetime.fromisoformat(data["created_at"])
        if data.get("batch_phase"):
            data["batch_phase"] = BatchPhase(data["batch_phase"])
        if data.get("status"):
            data["status"] = GenerationStatus(data["status"])
        data["last_qc_critique"] = _parse_json_field(data.get("last_qc_critique"), QCCritique)
        return Generation.model_validate(data)

    if model is Asset:
        if data.get("asset_type"):
            data["asset_type"] = AssetType(data["asset_type"])
        if data.get("retention_status"):
            data["retention_status"] = RetentionStatus(data["retention_status"])
        if data.get("expires_at"):
            data["expires_at"] = datetime.fromisoformat(data["expires_at"])
        if data.get("deleted_at"):
            data["deleted_at"] = datetime.fromisoformat(data["deleted_at"])
        return Asset.model_validate(data)

    if model is QCResult:
        if data.get("decision"):
            data["decision"] = QCDecision(data["decision"])
        return QCResult.model_validate(data)

    if model is CreditTrackerEntry:
        if data.get("logged_at"):
            data["logged_at"] = datetime.fromisoformat(data["logged_at"])
        return CreditTrackerEntry.model_validate(data)

    if model is GCLogEntry:
        if data.get("executed_at"):
            data["executed_at"] = datetime.fromisoformat(data["executed_at"])
        return GCLogEntry.model_validate(data)

    if model is PublishingQueueItem:
        if data.get("scheduled_at"):
            data["scheduled_at"] = datetime.fromisoformat(data["scheduled_at"])
        return PublishingQueueItem.model_validate(data)

    if model is BackupLogEntry:
        if data.get("executed_at"):
            data["executed_at"] = datetime.fromisoformat(data["executed_at"])
        return BackupLogEntry.model_validate(data)

    return model.model_validate(data)


def model_to_params(instance: BaseModel) -> dict:
    """Serialize a Pydantic model instance to a flat dict suitable for a
    parameterized SQL INSERT/UPDATE, JSON-encoding nested model fields."""
    data = instance.model_dump(mode="python")

    if isinstance(instance, Character):
        data["visual_signature"] = json.dumps(instance.visual_signature.model_dump(mode="python"))
        data["generation_control"] = json.dumps(instance.generation_control.model_dump(mode="python"))
        data["voice_profile"] = json.dumps(instance.voice_profile.model_dump(mode="python"))
        data["motion_profile"] = json.dumps(instance.motion_profile.model_dump(mode="python"))

    elif isinstance(instance, Episode):
        if instance.localized_metadata:
            data["localized_metadata"] = json.dumps(
                {k: v.model_dump(mode="python") for k, v in instance.localized_metadata.items()}
            )
        else:
            data["localized_metadata"] = None
        data["hold_from_gc"] = 1 if instance.hold_from_gc else 0
        if isinstance(instance.status, EpisodeStatus):
            data["status"] = instance.status.value

    elif isinstance(instance, Generation):
        if isinstance(instance.batch_phase, BatchPhase):
            data["batch_phase"] = instance.batch_phase.value
        if isinstance(instance.status, GenerationStatus):
            data["status"] = instance.status.value
        if instance.last_qc_critique:
            data["last_qc_critique"] = json.dumps(instance.last_qc_critique.model_dump(mode="python"))
        else:
            data["last_qc_critique"] = None

    elif isinstance(instance, Asset):
        if isinstance(instance.asset_type, AssetType):
            data["asset_type"] = instance.asset_type.value
        if isinstance(instance.retention_status, RetentionStatus):
            data["retention_status"] = instance.retention_status.value

    elif isinstance(instance, QCResult):
        if isinstance(instance.decision, QCDecision):
            data["decision"] = instance.decision.value

    elif isinstance(instance, Project):
        pass

    elif isinstance(instance, Scene):
        pass

    elif isinstance(instance, CreditTrackerEntry):
        pass

    elif isinstance(instance, GCLogEntry):
        pass

    elif isinstance(instance, PublishingQueueItem):
        pass

    elif isinstance(instance, BackupLogEntry):
        pass

    return data
