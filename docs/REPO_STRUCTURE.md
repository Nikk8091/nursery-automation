# Repository Structure

```
/nursery-factory/
├── .cursorrules                  # AI rules (Cursor) — duplicate of docs/AI_RULES_CANONICAL.md
├── .windsurfrules                # AI rules (Windsurf) — duplicate
├── CLAUDE.md                     # AI rules (Claude Code CLI) — duplicate
├── system_prompt.md              # AI rules (generic API/LLM scripts) — duplicate
├── README.md
├── pyproject.toml
├── .env.example                  # OLLAMA_HOST, COMFYUI_HOST, YT_OAUTH secrets, WEBHOOK_URL, RCLONE remotes
│
├── docs/
│   ├── AI_RULES_CANONICAL.md     # source of truth for the 4 duplicated rule files
│   ├── REPO_STRUCTURE.md         # this file
│   ├── PHASE2_ROADMAP.md         # Task 4: implementation checklist + Module 1 interface
│   └── specs/
│       ├── spec_01_vram_architecture.md      # hardware lock, batch phases, ComfyUI backend, OOM handling
│       ├── spec_02_database_schema.md        # ERD, SQLite DDL, Pydantic v2 models
│       ├── spec_03_agents_statemachine.md    # FSM, chunked handoff, QC scoring, webhooks
│       ├── spec_04_comfyui_workflows.md      # workflow JSON contract, node mutation, character consistency
│       ├── spec_05_audio_beat_assembly.md    # ACE-Step, Demucs, onset extraction, beat-driven assembly
│       └── spec_06_publishing_backups.md     # YouTube API, COPPA, GC cron, rclone split backup
│
├── comfyui/
│   └── workflows/                 # versioned, parameterized ComfyUI graphs (spec_04)
│       ├── sdxl_still.json
│       ├── thumbnail_still.json
│       └── wan22_i2v.json
│
├── engine/                        # low-level external-service clients (one per integration)
│   ├── __init__.py
│   ├── comfy_client.py            # <-- MODULE 1 (this turn's recommended starting point... see note below)
│   ├── ace_step_client.py         # ACE-Step song synthesis wrapper
│   ├── kokoro_client.py           # Kokoro narration TTS wrapper
│   ├── demucs_client.py           # stem separation wrapper (spec_05)
│   ├── onset_extractor.py         # librosa/aubio onset-grid extraction (spec_05)
│   ├── ffmpeg_assembly.py         # Beat-Driven Assembly cut/mux/subtitle logic (spec_05)
│   ├── ollama_client.py           # local LLM calls: planning, QC rubric, translation
│   └── youtube_client.py          # OAuth2 + videos.insert + thumbnails.set (spec_06)
│
├── agents/                        # orchestration layer — one module per agent (spec_03)
│   ├── __init__.py
│   ├── creative_director.py       # Phase A1
│   ├── storyboard_agent.py        # Phase A2
│   ├── character_agent.py
│   ├── visual_prompt_agent.py     # Phase C job builder + QC-critique retry logic
│   ├── audio_music_agent.py       # Phase B
│   ├── narration_agent.py
│   ├── video_gen_agent.py         # Phase D job builder
│   ├── assembly_agent.py          # Beat-Driven Cut Sync
│   ├── qc_agent.py                # scoring engine + structured critique emission
│   ├── asset_manager.py           # GC, hold_from_gc, backups, webhook alerting
│   └── publishing_agent.py        # cadence cap, thumbnail attach, localization
│
├── orchestration/
│   ├── __init__.py
│   ├── state_machine.py           # deterministic FSM (spec_03 §3)
│   └── batch_executor.py          # phase-boundary discipline + OOM halving (spec_01 §4)
│
├── db/
│   ├── __init__.py
│   ├── models.py                  # Pydantic v2 models (spec_02 §3) — MODULE 1, recommended start
│   ├── schema.sql                 # SQLite DDL (spec_02 §2)
│   ├── session.py                 # connection/session helpers
│   └── migrations/
│       └── 0001_init.sql
│
├── characters/                    # RUNTIME DATA — persistent character bibles (gitignored except .gitkeep)
│   └── .gitkeep
├── projects/                      # RUNTIME DATA — episode working directories (gitignored)
│   └── .gitkeep
├── models/                        # local model weights (gitignored, large binary files)
│   └── .gitkeep
│
├── scripts/                        # cron entrypoints + manual CLI triggers
│   ├── run_gc.py                   # spec_06 §1 — hourly/daily GC
│   ├── run_backup.py               # spec_06 §2 — rclone split sync
│   └── run_pipeline.py             # manual single-episode trigger, useful for MVP validation
│
├── logs/
│   └── .gitkeep
│
└── tests/
    ├── __init__.py
    ├── test_comfy_client.py
    ├── test_state_machine.py
    └── test_db_models.py
```

## Directory-to-Spec Ownership Map

| Directory/file | Owning spec(s) |
|---|---|
| `engine/comfy_client.py` | spec_01, spec_04 |
| `engine/ace_step_client.py`, `kokoro_client.py`, `demucs_client.py`, `onset_extractor.py`, `ffmpeg_assembly.py` | spec_05 |
| `engine/ollama_client.py` | spec_03 (planning, QC), spec_06 (translation) |
| `engine/youtube_client.py` | spec_06 |
| `agents/*` | spec_03 (roster + logic), spec_04 (image/video agents), spec_05 (audio agent), spec_06 (publishing agent) |
| `orchestration/state_machine.py` | spec_03 §3 |
| `orchestration/batch_executor.py` | spec_01 §2, §4 |
| `db/models.py`, `db/schema.sql` | spec_02 |
| `comfyui/workflows/*.json` | spec_04 §2 |
| `scripts/run_gc.py`, `scripts/run_backup.py` | spec_06 §1, §2 |

Runtime data directories (`characters/`, `projects/`, `models/`, `logs/`) mirror the **data** directory layout specified in spec_02/spec_06 — they are not source code and should be `.gitignore`d apart from `.gitkeep` placeholders (and `models/` should never be committed — weights are fetched separately per operator setup).
