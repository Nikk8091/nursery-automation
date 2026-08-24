# Spec 05 — Audio Synthesis & Beat-Driven Assembly
**Source:** decomposed from Master Blueprint v2.2, §2.4, §2.6, §3.1 (Phase B), §6. Owns: ACE-Step/Kokoro integration, Demucs stem isolation, onset/transient extraction, FFmpeg Beat-Driven Assembly rules.

---

## 1. Component Stack

| Component | Tag | License | Role |
|---|---|---|---|
| ACE-Step 1.5 | `[OSL]` | MIT | Sung verses/chorus — native `lyric2vocal` singing synthesis, 50+ languages. <4–12GB VRAM |
| Kokoro-82M | `[OSL]` | Apache-2.0 | Spoken narration bridges. CPU-viable |
| Demucs (Meta) | `[OSL]` | MIT | Local percussive/vocal stem separation — new in v2.2 |
| librosa / aubio | `[OSL]` | ISC / GPL-2.0+ | Onset/transient detection on the isolated stem |
| FFmpeg | `[OSL]` | — | Onset-aligned cut trimming, mux, subtitle burn-in |

**Explicitly excluded (non-commercial on free tier):** Suno `[PNC]`, Udio `[PNC]`, MusicGen (Meta, CC-BY-NC) `[PNC]`. Do not call these for production output under any code path.

**Lip-sync is fully descoped** — no zero-cost commercial-safe solution exists. Do not implement viseme/phoneme-driven mouth animation. All "does the character look like it's performing" is solved via Beat-Driven Assembly (§4) instead.

---

## 2. Phase B Pipeline Order (hard sequence)

```
1. ACE-Step 1.5 generates the full song (all verses/chorus) from Phase A2 lyrics
2. Kokoro generates all narration lines
3. Demucs isolates percussive + vocal stems from the ACE-Step mix  <-- MUST run before step 4
4. librosa/aubio onset/transient extraction runs against the ISOLATED stem, not the raw mix
5. Resulting onset grid persisted to episode audio/song/ directory + referenced by GENERATIONS
```

**Why step 3 precedes step 4:** dense melodic synth layers common in nursery-rhyme backing tracks trigger false-positive onset flags in librosa/aubio's peak-picking when run against the full mix. Isolating percussive/vocal content first produces a materially cleaner grid. **Do not skip the Demucs pass as an optimization — it is a correctness step, not a nice-to-have.**

Demucs runs efficiently on CPU for track lengths in this project's range (60–90s) and adds negligible VRAM burden on top of the Tier 3 budget — it does not require GPU residency management like SDXL/Wan 2.2 (spec_01).

---

## 3. Onset/Transient Grid Extraction

**Method:** onset-strength envelope + peak-picking (librosa `onset.onset_strength` + `onset.onset_detect`, or aubio's `onset` object) run against the Demucs-isolated stem.

**Priority: dynamic onsets over static BPM.** Do not compute a single averaged tempo value and generate a fixed-interval cut grid from it. Nursery rhymes are rarely perfectly metronomic (pickup notes, held syllables, rubato phrasing) — a static BPM grid produces visibly robotic pacing. The onset grid is a **list of timestamps**, not a tempo scalar.

**Output contract:**
```json
{
  "onset_timestamps_sec": [0.42, 0.98, 1.55, 2.10, 2.71, ...],
  "source_stem": "audio/song/isolated_vocal_percussive.wav",
  "extraction_method": "librosa_onset_strength_peak_picking",
  "episode_id": "ep014"
}
```
Stored under `projects/<project_id>/episodes/<episode_id>/audio/song/` alongside the ACE-Step stems (persistent, exempt from GC — spec_06).

---

## 4. Beat-Driven Assembly (Assembly Agent, post-Phase D)

**Replaces lip-sync entirely.** Two things key off the onset grid:

1. **Scene cuts:** the Assembly Agent snaps each scene-cut point in the FFmpeg edit to the nearest onset timestamp in the grid, rather than using fixed clip durations.
2. **Body-bob motion:** the Wan 2.2 motion prompt (Phase D, spec_04) bakes in a stylized body-bob animation cycle via the Character Bible's `motion_profile.beat_bob_intensity`, keyframed to onset timestamps rather than a metronomic interval. No mouth/viseme rig is generated or required.

**QC gate (Beat-Sync Accuracy, spec_03 §6):** the QC Agent scores the percentage of realized scene cuts landing within **±80ms** of an actual onset event in the grid. This is a hard-gated metric (weight 20/100), not advisory.

**FFmpeg implementation notes:**
- Cut points computed in Python from the onset grid, then passed to FFmpeg as explicit segment boundaries (e.g., via a concat demuxer file with computed `duration` values per segment) — not derived from FFmpeg's own scene-detection filters, which are unrelated to the audio onset grid and would defeat the purpose.
- Subtitle burn-in and final mux happen in the same Assembly pass, after cut points are finalized.

## 5. Cross-References
- Character Bible `motion_profile` schema: **spec_04 §4**.
- Beat-Sync Accuracy scoring/thresholds: **spec_03 §6**.
- Persistent audio directory exemption from GC: **spec_06 §1**.
