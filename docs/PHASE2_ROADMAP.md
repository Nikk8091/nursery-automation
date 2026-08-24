# Phase 2 Implementation Roadmap

## Which module to build first, and why

Two reasonable candidates: `db/models.py` and `engine/comfy_client.py`. **Build `db/models.py` first.**

Reasoning:
1. **It's the shared contract everything else depends on.** Every agent, the state machine, and `comfy_client.py`'s own job-tracking all read/write these models (Rule 3 in the canonical AI rules: no raw dicts crossing a module boundary). Building it first means `comfy_client.py`'s method signatures can reference concrete types (`Generation`, `BatchPhase`) instead of guessing the shape and refactoring later.
2. **Zero external dependencies.** No GPU, no running ComfyUI server, no network access required to write and fully unit-test this module. It's the fastest module to get to 100% test coverage, which de-risks the rest of the build.
3. **It's declarative, not procedural.** For a Pydantic model file, the "interface" and the "implementation" are the same artifact — there's no partial-stub version that's meaningfully less work than the finished version. It's naturally a complete-in-one-sitting task.

**Immediately after `db/models.py`: build `engine/comfy_client.py`.** This is the highest-risk external integration in the whole system — validating that a real ComfyUI daemon actually behaves as spec_04 assumes (workflow mutation, `/history` polling, OOM error shape) should happen early, before any agent code is written against assumptions that might be wrong. Its interface stub is already scaffolded at `engine/comfy_client.py` in this package — implement the method bodies against a locally running ComfyUI instance as your first coding session.

## Step-by-step order

1. **`db/models.py`** — implement as delivered in this package (already complete — see file). Write `tests/test_db_models.py` covering: enum round-tripping, `Episode.localized_metadata` nested-model validation, `Generation.last_qc_critique` optionality.
2. **`db/schema.sql` + `db/session.py`** — stand up a local SQLite file from the DDL; write `session.py` helpers (`get_connection()`, `row_to_model()` mapping raw rows to the Pydantic models from step 1).
3. **`engine/comfy_client.py`** — implement the stubbed methods in this package against a real local ComfyUI instance at Tier 3 (8GB, `--lowvram`). Validate against `comfyui/workflows/sdxl_still.json` first (cheaper/faster to iterate on than video). Write `tests/test_comfy_client.py` with a mocked HTTP layer for CI, plus a manual/integration test marked `@pytest.mark.requires_gpu` for real hardware runs.
4. **`orchestration/batch_executor.py`** — implement the OOM adaptive-halving loop (spec_01 §4) as a standalone, testable function that takes a job list + `ComfyUIClient` and returns per-item results — build this before wiring the full state machine, since it's independently testable with a mocked client that simulates OOM on the first N calls.
5. **`orchestration/state_machine.py`** — implement the FSM from spec_03 §3. Cross-check every transition against the Mermaid diagram in that spec. Write `tests/test_state_machine.py` asserting the phase-boundary discipline (rule 2 in the canonical AI rules) is structurally impossible to violate.
6. **`engine/ollama_client.py`** — planning calls (Phase A1/A2), QC rubric pass, and translation step (spec_06 §5) share one thin Ollama wrapper.
7. **`agents/creative_director.py` + `agents/storyboard_agent.py`** — Phase A1/A2 chunked handoff (spec_03 §2), consuming `ollama_client.py`.
8. **`engine/ace_step_client.py`, `demucs_client.py`, `onset_extractor.py`** — Phase B stack (spec_05 §2, §3), in that dependency order (song → stem isolation → onset extraction).
9. **`agents/visual_prompt_agent.py`, `agents/video_gen_agent.py`** — Phase C/D job builders wrapping `comfy_client.py`, including the QC-critique retry logic (spec_03 §4.1).
10. **`agents/qc_agent.py`** — scoring engine (spec_03 §6) — build after visual/video agents exist so there's real output to score against.
11. **`engine/ffmpeg_assembly.py` + `agents/assembly_agent.py`** — Beat-Driven Assembly (spec_05 §4).
12. **`agents/asset_manager.py` + `scripts/run_gc.py` + `scripts/run_backup.py`** — GC/`hold_from_gc`/webhook/backup (spec_06 §1, §2).
13. **`engine/youtube_client.py` + `agents/publishing_agent.py` + `scripts/run_pipeline.py`** — publishing, cadence cap, localization (spec_06 §3–§5) — last, since it's the only module touching a real external account with real-world consequences (OAuth2, live uploads).

## Module 1 — Exact Interface

`db/models.py` is delivered complete in this package (`db/models.py`). See that file for the full Pydantic v2 model set: `Project`, `Character` (+ nested `CharacterVisualSignature`, `CharacterGenerationControl`, `CharacterVoiceProfile`, `CharacterMotionProfile`), `Episode` (+ `LocalizedMetadataEntry`), `Scene`, `Generation` (+ `QCCritique`), `Asset`, `QCResult`, `CreditTrackerEntry`, `GCLogEntry`, `PublishingQueueItem`, `BackupLogEntry`, and the six enums (`EpisodeStatus`, `BatchPhase`, `GenerationStatus`, `AssetType`, `RetentionStatus`, `QCDecision`).

`engine/comfy_client.py` is delivered as an **interface stub** (method signatures + docstrings, `NotImplementedError` bodies) in this package — implement its bodies as your first coding session per step 3 above.
