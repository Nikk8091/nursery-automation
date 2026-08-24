# Spec 06 — Publishing, Compliance, GC Cron & Offsite Backup
**Source:** decomposed from Master Blueprint v2.2, §9, §8.3. Owns: YouTube API contract, COPPA/monetization policy, GC cron operational logic, `rclone` split backup jobs.

---

## 1. Ephemeral GC Cron (operational — schema fields defined in spec_02)

**Policy:**
- `ASSETS` rows with `retention_status='ephemeral'` AND `status='rejected'` → eligible for immediate soft-deletion, any age.
- `ASSETS` rows with `retention_status='ephemeral'` AND `age > 48h` → eligible for soft-deletion.
- **Before evaluating either rule, check `EPISODES.hold_from_gc` for the parent episode.** If `true`, skip the entire episode's working directory unconditionally, log a `GC_LOG` row with `action='skipped_hold'`.
- **Unconditionally exempt** (independent of hold flag): `characters/*/references/`, `script/`, `thumbnail/`, `audio/`, `qc/`, `final/`.

**Two-stage delete:**
```
# Hourly — soft delete
0 * * * *  python scripts/run_gc.py --stage soft --ttl-hours 48

# Daily, 24h offset from soft-delete — hard delete
0 3 * * *  python scripts/run_gc.py --stage hard --trash-age-hours 24
```
- **Soft stage:** mark `deleted_at`, move file to `_trash/` staging path, log `GC_LOG(action='soft_delete')`.
- **Hard stage:** permanently unlink anything in `_trash/` older than 24h, log `GC_LOG(action='hard_delete')`. The 24h gap between soft and hard delete is the operator's recovery window.

**Stale-hold monitoring:** `run_gc.py` should additionally warn (log + optional webhook, §4 below) on any episode with `hold_from_gc=true` and `age > 30 days` — a forgotten hold otherwise causes indefinite disk growth.

---

## 2. Offsite Backup — Split Policy (Google Drive + Cloudflare R2)

| Directory | Remote | Rationale |
|---|---|---|
| `characters/` | Google Drive (free 15GB) | Small, text/JSON-heavy, comfortably within free tier |
| `db/` | Google Drive (free 15GB) | Same as above |
| `final/` | **Cloudflare R2** | S3-API-compatible (`rclone` `s3` backend, `provider=Cloudflare`), **zero egress fees** — avoids Glacier's retrieval cost/latency for the directory most likely to need fast restoration after a local drive failure. Free tier: 10GB storage, no egress at any tier |

**Daily cron (third maintenance stage, after GC soft/hard-delete):**
```
0 4 * * *  rclone sync /nursery-factory/characters gdrive:nursery-backup/characters --checksum
0 4 * * *  rclone sync /nursery-factory/db           gdrive:nursery-backup/db           --checksum
0 4 * * *  rclone sync /nursery-factory/final         r2:nursery-backup/final           --checksum
```
- `characters/` and `db/` sync in full each run (small). `final/` relies on `rclone`'s checksum-diff so only newly-published episodes transfer — daily bandwidth bounded to that day's new output.
- Every run logs to `BACKUP_LOG` (spec_02): `source_path`, `remote_path`, `status`, `bytes_transferred`, `executed_at`.
- **Failure alerting:** non-zero `rclone` exit code routes through the same webhook mechanism as spec_03 §5 — do not build a second notification path.

**`rclone` remote config (one-time setup, not per-run):**
```
rclone config create gdrive drive
rclone config create r2 s3 provider=Cloudflare access_key_id=<...> secret_access_key=<...> endpoint=<account-id>.r2.cloudflarestorage.com
```

---

## 3. COPPA / Made for Kids Compliance

- `status.madeForKids = true` and `status.selfDeclaredMadeForKids = true` **mandatory** on every `videos.insert` call for this channel — no code path may omit these fields.
- `status.selfCertification` for AI-altered/synthetic content **mandatory** — required since May 2025 enforcement, independent of the kids flag.
- EU reachability: if the channel is reachable in the EU, EU AI Act Article 50 creator-disclosure obligations apply (from Aug 2, 2026) regardless of operator location — surface this as a publish-time checklist item, not silently assumed.

**Finalized monetization posture:** slow, brand-safe cadence. Scheduling logic (§4 below) treats `max_uploads_per_day` as a **ceiling that will typically go unused**, not a target — prioritize audience retention / brand-deal readiness / Shorts fund eligibility over aggressive YPP ad-revenue scaling. Do not implement any "maximize upload frequency" optimization logic in the Publishing Agent.

---

## 4. YouTube Data API v3 — Quota & Cadence Contract

**Quota mechanics:** `videos.insert` draws from its own dedicated ~100-calls/day bucket (post-June-2026 restructuring). `search.list` (100 units/call) and most other read/write ops still draw from the shared 10,000-unit pool. **Both buckets must be respected — do not assume either is unlimited.**

**Hard-coded Publishing Agent limits (enforce in code, not just docs):**
```python
MAX_UPLOADS_PER_DAY = 2
MIN_INTERVAL_BETWEEN_UPLOADS_HOURS = 8
# search.list is NEVER called from the automated pipeline — interactive/manual use only
```
The binding constraint is **algorithmic risk** (YouTube's Inauthentic Content enforcement is channel-level, per Jan 2026 termination wave precedent), not quota headroom — the cadence cap must not be relaxed even if quota usage is well under budget.

---

## 5. Automated OAuth2 Publishing Workflow

```
1. OAuth2 consent (one-time; refresh_token stored as a secret, never committed)
2. Publishing Agent checks cadence cap (MAX_UPLOADS_PER_DAY, MIN_INTERVAL_BETWEEN_UPLOADS_HOURS)
   -> if cap exceeded, queue for next eligible window, do not force through
3. Construct `snippet` (title, description, tags) from Phase A1/A2 outputs
4. Construct `status` (madeForKids, selfDeclaredMadeForKids, selfCertification)
5. Prompt local Ollama to translate title/description into target-language list
   (default: es, hi) -> populate `localizations` map
   -> persist to EPISODES.localized_metadata for audit
6. Attach dedicated Phase C thumbnail via EPISODES.thumbnail_asset_id -> thumbnails.set
7. Resumable upload via videos.insert
8. Log result to PUBLISHING_QUEUE (spec_02)
```

**`localizations` payload example:**
```json
"localizations": {
  "es": {"title": "...", "description": "..."},
  "hi": {"title": "...", "description": "..."}
}
```
Default language list is a configuration value, not hardcoded — extendable without a code change.

**Never:** bypass CAPTCHA, fake OAuth consent flows, or batch-upload faster than the channel's organic growth would plausibly support, regardless of remaining quota.

## 6. Cross-References
- `EPISODES.hold_from_gc`, `localized_metadata`, `ASSETS` retention fields: **spec_02**.
- Webhook payload contract shared with GC/backup failure alerts: **spec_03 §5**.
