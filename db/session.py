"""
db/session.py — SQLite connection/session helpers.

Source of truth: docs/specs/spec_02_database_schema.md.
Interface stub — implement bodies as part of Phase 2 step 2 (see
docs/PHASE2_ROADMAP.md).
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Type, TypeVar

from pydantic import BaseModel

ModelT = TypeVar("ModelT", bound=BaseModel)

DEFAULT_DB_PATH = Path("db/nursery_factory.db")


def init_db(db_path: Path = DEFAULT_DB_PATH, schema_path: Path = Path("db/schema.sql")) -> None:
    """Apply db/schema.sql to a fresh SQLite file if it doesn't already exist."""
    raise NotImplementedError


@contextmanager
def get_connection(db_path: Path = DEFAULT_DB_PATH) -> Iterator[sqlite3.Connection]:
    """Context-managed SQLite connection with row_factory set for dict-like
    row access (required by row_to_model below)."""
    raise NotImplementedError


def row_to_model(row: sqlite3.Row, model: Type[ModelT]) -> ModelT:
    """Map a raw sqlite3.Row to the given Pydantic model from db/models.py,
    parsing any JSON-encoded columns (visual_signature, localized_metadata,
    last_qc_critique, etc.) before validation."""
    raise NotImplementedError


def model_to_params(instance: BaseModel) -> dict:
    """Serialize a Pydantic model instance to a flat dict suitable for a
    parameterized SQL INSERT/UPDATE, JSON-encoding nested model fields."""
    raise NotImplementedError
