# Nursery Rhyme Video Factory

Autonomous, $0-recurring-cost, commercially-safe nursery rhyme video pipeline for YouTube.

**Start here:**
1. `docs/REPO_STRUCTURE.md` — full directory map + spec ownership table
2. `docs/specs/spec_01` through `spec_06` — the locked Phase 1 architecture (do not redesign; implement)
3. `docs/PHASE2_ROADMAP.md` — build order + Module 1 (`db/models.py`) + Module 2 (`engine/comfy_client.py`)
4. `.cursorrules` / `CLAUDE.md` / `.windsurfrules` / `system_prompt.md` — identical AI coding-assistant guardrails; whichever tool you use will pick up the matching file automatically

**Hardware target:** Tier 3, 8GB VRAM (see spec_01). ComfyUI must be running headless (`--lowvram`) before any generation code will function — see spec_01 §3 for launch instructions.
