# Spec 01 — VRAM & Batch-Execution Architecture
**Source:** decomposed from Master Blueprint v2.2, §3, §3.1, §3.2, §7. Owns: hardware tier lock, batch phase boundaries, headless ComfyUI backend contract, adaptive OOM handling.

---

## 1. Locked MVP Hardware Target

| Parameter | Value |
|---|---|
| Tier | **Tier 3 — 8GB VRAM** (e.g. RTX 4060 Ti / 3070) |
| ComfyUI launch flag | `--lowvram` |
| Video engine | **Wan 2.2 TI2V-5B** (Apache-2.0), 720p/24fps |
| Image engine | SDXL 1.0 primary, FLUX.1-schnell fallback |
| Realistic weekly output | 2–3 episodes/week |

Tier 1 (no GPU) is excluded from production scope — commercially-licensed local video/image generation is not viable without a GPU. Tier 2 (4–6GB, `--novram`) is a documented degraded fallback (480p ceiling, 1 ep/week). Tier 4 (12GB+, default/`--highvram`) is an optional speed upgrade, not a requirement.

**Rationale for the lock:** Wan 2.2 TI2V-5B is the only Apache-2.0 (unconditional commercial use) video model that fits an 8GB card at 720p without quantization workarounds. All Phase 2 code must be written and tested against this configuration first.

---

## 2. Batch-Execution Paradigm (the core VRAM-thrashing fix)

**Problem this solves:** naive per-scene sequential generation forces repeated model load/unload cycles (SDXL ↔ Wan 2.2 ↔ ACE-Step), each costing tens of seconds to low minutes on an 8GB card — paid 10–20 times per episode in a naive design.

**Solution:** four strictly sequential batch phases per episode. Model loads are fixed at **exactly 2 per episode**, regardless of scene count, LLM chunking granularity, thumbnail inclusion, or OOM-driven batch resizing.

| Phase | Owner | GPU/VRAM cost | Model resident |
|---|---|---|---|
| **A1 — Master Outline** | Creative Director Agent | None (LLM/CPU only) | — |
| **A2 — Chunked Storyboard** | Storyboard Agent | None (LLM/CPU only) | — |
| **B — Batch Audio** | Audio/Music Agent | Low (ACE-Step <4–12GB, Kokoro CPU-viable, Demucs CPU) | ACE-Step, briefly |
| **C — Batch Image Gen** | Visual Prompt Agent → ComfyUI | High | SDXL/FLUX, loaded **once** |
| *(model swap)* | Asset Manager / orchestrator | — | SDXL unloaded, Wan 2.2 loaded |
| **D — Batch Video Gen** | Video Gen Agent → ComfyUI | High | Wan 2.2, loaded **once** |

**Phase boundary rule (hard constraint):** the orchestrator MUST NOT begin Phase D for an episode until every item in that episode's Phase C batch (all scene stills + the dedicated thumbnail, including retries) has resolved to `approved` or `manually_flagged`. A single video-model load event must never overlap with an unresolved image batch.

**Phase A detail — Chunked Handoff** (protects LLM prompt fidelity, not a VRAM concern but part of the same phase-boundary discipline):
1. **A1:** one compact LLM call → episode structural skeleton (scene count, arc beats, character list). Terse output, not a shot list.
2. **A2:** Storyboard Agent iterates the A1 outline **scene-by-scene**, one focused LLM call per scene (or 2–3 scene chunks), each carrying only the outline + that scene's context — not the full accumulated episode detail. Character Agent resolves Character Bible references in the same pass. All per-scene prompts combine into one ordered queue = Phase C's batch input.
- Chunking trades a few extra LLM calls for materially higher prompt fidelity vs. asking one call for 15–20 fully-detailed mutually-consistent prompts at once. Does not affect the fixed 2-model-load guarantee.

**Phase B detail — audio ahead of onset extraction (full spec: spec_05):**
ACE-Step (song) + Kokoro (narration) generate in one pass → Demucs isolates percussive/vocal stems from the ACE-Step mix → librosa/aubio onset/transient extraction runs against the **isolated stem**, not the raw mix.

**Phase C detail:**
ComfyUI loads SDXL/FLUX once, processes the *entire* queue from A2 — every scene still **plus one dedicated 16:9 thumbnail job** — using Character Bible seed/IP-Adapter locks (full spec: spec_04). Checkpoint stays resident for the whole batch except at an OOM-triggered halving (below).

**Phase D detail:**
Only after the full Phase C batch (incl. thumbnail) passes the CLIP-similarity Character Consistency Gate does ComfyUI unload SDXL and load Wan 2.2 once, then process the full image-to-video job queue.

---

## 3. Headless ComfyUI Backend Contract

**All generative inference (SDXL/FLUX stills, thumbnail, Wan 2.2 video) MUST route through ComfyUI's REST API. Direct `torch`/`diffusers`/local inference calls inside pipeline Python code are prohibited** (enforced at the tooling-rules level, see the canonical AI rules file).

**Launch:**
```
python main.py --listen 0.0.0.0 --port 8188 --lowvram
```

**API surface used by the orchestrator:**
| Endpoint | Method | Purpose |
|---|---|---|
| `/prompt` | POST | Enqueue a mutated workflow JSON (see spec_04 for template structure and node-mutation rules) |
| `/history/{prompt_id}` | GET | Poll job status: `queued` / `running` / `complete` / `failed` / `oom` |
| `/view` | GET | Retrieve completed output (image/video file) |
| `/queue` | GET | Inspect current queue depth (used for batch-completion bookkeeping) |

**VRAM management is delegated to ComfyUI itself, not the orchestrator.** ComfyUI's smart model-caching keeps exactly one checkpoint resident at a time and evicts it only at the explicit Phase C→D model-swap boundary. `--lowvram`/`--novram` (ComfyUI's equivalents of Automatic1111's classic `--medvram`/`--lowvram` offload behavior) control how aggressively layers offload to system RAM under memory pressure. The orchestrator never manually tracks GPU memory state — it only tracks phase/batch progress via the `GENERATIONS` table (spec_02).

Every job's status, including `comfyui_prompt_id`, is persisted to `GENERATIONS` on every poll — this is what makes a ComfyUI crash mid-batch **resumable**: a restarted batch continues from the last incomplete item instead of re-running the whole phase.

---

## 4. Adaptive Batch Sizing on OOM (v2.2)

**Trigger:** a `/history/{prompt_id}` poll returns an OOM error (or the pipeline's HTTP client catches an HTTP 500 from ComfyUI correlated with an OOM condition) for any in-flight item during Phase C or Phase D.

**Algorithm:**
```
batch_size = full_batch
attempt = 0
while item fails with OOM and attempt < 2:
    batch_size = batch_size // 2
    resubmit remaining queue items at batch_size
    attempt += 1
if item still OOMs at batch_size == 1 (or after 2 halvings):
    route item to ManualFlag -> HumanReview
```

- **Halving floor: 2 halvings maximum** (full → 1/2 → 1/4). Beyond that, escalate rather than loop indefinitely.
- This is orthogonal to artifact/consistency-quality retries (spec_03) — OOM is an infrastructure failure, handled *before* quality gating is attempted on the affected items.
- Every attempt's resulting `batch_size_used` is persisted to `GENERATIONS.batch_size_used` (spec_02) for tuning real-world Tier 3 VRAM headroom over time.
- Occasional OOM at Tier 3 under heavy retry load is **expected behavior**, already budgeted inside the 20–30% retry overhead in the resource-math table (spec_02/spec_03), not a hard failure mode.

---

## 5. Resource Math (per 90s episode, batch/model-load accounting)

| Batch Phase | Asset | Base | With retry/OOM overhead |
|---|---|---|---|
| A1 | Master outline | 1 LLM call | 1 (rarely retried) |
| A2 | Chunked storyboard prompts | ~12 calls | ~14–16 |
| B | Sung verses (ACE-Step) | 2–4 sections | 3–5 |
| B | Narration lines (Kokoro) | ~10 lines | ~10–12 |
| B | Stem separation (Demucs) | 1 pass | 1 (deterministic, no retry) |
| C | Scene stills (SDXL) | ~12 shots | ~15–16 (+ occasional OOM resubmission) |
| C | Dedicated thumbnail | 1 shot | 1–2 |
| D | Video clips (Wan 2.2, 5s ea.) | ~18 clips | ~22–24 (+ occasional OOM resubmission) |

**Model loads per episode: fixed at 2**, independent of every retry/chunking/OOM variable above.

---

## 6. Cross-References
- Database fields referenced here (`GENERATIONS.batch_size_used`, `comfyui_prompt_id`, `status`): **spec_02**.
- State-machine integration of the OOM states (`OOM_Halve_C`, `OOM_Halve_D`) and their transitions: **spec_03**.
- ComfyUI workflow JSON templates and node mutation contract: **spec_04**.
- GPL-3.0 licensing rationale for the REST-API-only integration pattern: see canonical AI rules file and spec_04 header note.
