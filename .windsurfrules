# Nursery Rhyme Video Factory — AI Coding Assistant Rules
**This file is the canonical source. It is duplicated verbatim as `.cursorrules`, `CLAUDE.md`, `.windsurfrules`, and `system_prompt.md` at the repo root. If you edit one, sync all four.**

You are working inside the `/nursery-factory/` codebase. This is a Phase 2 implementation of a locked architecture (see `docs/specs/spec_01` through `spec_06`). The architecture is **finalized** — your job is to implement it correctly, not to redesign it. If a requested change conflicts with a rule below, stop and flag the conflict instead of silently working around it.

---

## Non-Negotiable Rules

### 1. Strict VRAM Isolation
- **NEVER** call `torch`, `diffusers`, `transformers` model-loading/inference APIs, or any local model-inference script directly inside pipeline Python code for image or video generation.
- **ALL** SDXL/FLUX/Wan 2.2 generation MUST go through HTTP REST calls to the headless ComfyUI daemon (`POST /prompt`, `GET /history/{id}`, `GET /view`). See `docs/specs/spec_01_vram_architecture.md` and `docs/specs/spec_04_comfyui_workflows.md`.
- If a task seems to require direct model inference in Python, it is out of scope for this repo's pipeline code — flag it instead of implementing it.

### 2. Batch Boundary Enforcement
- The pipeline has exactly **4 sequential batch phases per episode**: A (A1 outline → A2 chunked storyboard) → B (audio) → C (all image gen incl. thumbnail) → D (all video gen). Model loads are fixed at **2 per episode**.
- **NEVER** write a per-scene loop that calls ComfyUI generation inside a `for scene in scenes:` block interleaved with other phase work. All Phase C jobs for an episode are built and submitted as one batch; all Phase D jobs likewise.
- **NEVER** begin Phase D work for an episode until every Phase C item (including retries and OOM-halving) has resolved to `approved` or `manually_flagged`.
- Full rules: `docs/specs/spec_01_vram_architecture.md` §2.

### 3. Strict Typing
- **ALL** data passed between agents, and between any agent and the database, MUST use the Pydantic v2 models defined in `docs/specs/spec_02_database_schema.md` §3 (implemented in `db/models.py`).
- No raw `dict` payloads crossing a module boundary. No bare tuples/lists standing in for structured records.
- Enum fields (`EpisodeStatus`, `BatchPhase`, `GenerationStatus`, `AssetType`, `RetentionStatus`, `QCDecision`) MUST use the `str, Enum` classes provided — not free-text strings compared with `==`.

### 4. OOM Recovery
- Every call site that polls ComfyUI (`GET /history/{id}`) during Phase C or Phase D MUST wrap the poll in a `try/except` that catches HTTP 500 responses / OOM-indicating errors.
- On catching an OOM: halve the remaining batch size for that phase and resubmit, **maximum 2 halvings** (full → 1/2 → 1/4). If still failing at batch size 1, route the item to `ManualFlag`/`HumanReview` — do not retry indefinitely.
- Log `batch_size_used` on every `Generation` record for this phase, per `docs/specs/spec_01_vram_architecture.md` §4.

---

## Additional Architectural Invariants

- **Licensing tags matter.** Only call generation services tagged `[OSL]` (open-source/local) or `[VFA]` (verified free API) for production output. Never call Suno, Udio, MusicGen, Luma, Kling, or Hailuo for anything that ships to the channel — they are ToS-restricted to non-commercial/manual previz only, per `docs/specs/spec_05_audio_beat_assembly.md` §1.
- **No lip-sync / viseme code.** This is intentionally descoped. Character "performance" is solved via onset-driven Beat-Driven Assembly (`docs/specs/spec_05_audio_beat_assembly.md` §4), not mouth animation. Do not add phoneme-to-viseme mapping code.
- **Onsets, not static BPM.** Beat-sync logic must key off the extracted onset-timestamp list, never a single averaged-BPM fixed-interval grid. Demucs stem isolation MUST run before onset extraction — do not run librosa/aubio against the raw ACE-Step mix.
- **`hold_from_gc` is authoritative.** Any garbage-collection or deletion logic MUST check `EPISODES.hold_from_gc` on the parent episode before evaluating an asset for purge. This check cannot be optimized away or cached stale.
- **Upload cadence is a hard cap, not a target.** `max_uploads_per_day = 2`, `min_interval_between_uploads_hours = 8`. Do not implement logic that maximizes upload frequency toward this cap — the finalized posture is slow/brand-safe (`docs/specs/spec_06_publishing_backups.md` §3).
- **`search.list` is never called from automated code.** Reserved for manual/interactive use only, to protect the shared 10,000-unit YouTube quota pool.
- **ComfyUI is a network service, not a library.** Do not `import` ComfyUI internals or vendor its source into this repo — this is what keeps the pipeline codebase outside ComfyUI's GPL-3.0 copyleft trigger (`docs/specs/spec_04_comfyui_workflows.md` §1). Flag any change that would require importing ComfyUI internals directly.
- **Workflow JSON templates are pure execution graphs.** Prompt engineering, retry decisions, and QC thresholds belong in agent/state-machine code (`docs/specs/spec_03`), never hardcoded into the `comfyui/workflows/*.json` files themselves.
- **Made for Kids / altered-content fields are mandatory**, not optional, on every `videos.insert` call — see `docs/specs/spec_06_publishing_backups.md` §3.

---

## When Implementing a Module

1. Locate the owning spec file in `docs/specs/` before writing code — each module maps to exactly one spec file's section (see `docs/REPO_STRUCTURE.md` for the mapping).
2. Match field names, enum values, and JSON payload shapes **exactly** as defined in the specs — do not invent alternate field names for convenience.
3. If a spec is ambiguous or silent on an implementation detail, prefer the narrowest interpretation consistent with the four non-negotiable rules above, and note the assumption in a code comment.
4. Do not "helpfully" add features, retry strategies, or scaling optimizations not described in the specs — the architecture has already been through several review cycles; unrequested scope changes are not welcome without discussion.
