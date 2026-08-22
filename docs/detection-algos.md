# Detection Algorithm Evaluation — nutsafe Phase 3

## Signal characteristics (what we're actually detecting)

Before comparing algorithms, we need to understand the data they run against.

**Latency (duration_ns)**
- Right-skewed. Most spans complete quickly; a small tail of slow ones drags the mean up.
- Not normally distributed. DB calls, HTTP calls, and cache hits each have their own shape.
- Baseline drifts gradually over time (traffic pattern changes, index bloat, code deploys).
- A regression looks like: many spans in a 5-min window suddenly averaging 10x the historical mean.

**Error rate**
- Binary per span (ERROR or not). Treated as a proportion, not a continuous value.
- Thresholded, not statistical — "more than 10% errors" is already meaningful regardless of distribution.

**Throughput**
- Count-based. Collapse is a ratio: current-minute vs 59-min rolling rate.
- Not a distribution problem — a simple ratio check is correct.

**Cascade**
- Derived signal: fires when 2+ keys are simultaneously anomalous.
- Not an algorithm decision — it's a compositional rule on top of other detectors.

**Key constraint:** the scheduler queries *window-level aggregates* (`avg_duration_ns` per span key) — not individual span durations. The algorithm sees one average per `(span_name, target, service_name)` per detection window, not a raw stream of points.

---

## Candidates

### 1. Welford + σ (current)

**Mechanism:** Online mean and variance (M2) updated per span at ingest time. At detection time, compute `stddev = sqrt(M2 / n)` and flag if `(avg_window - mean) / stddev > 3`.

**What the DB stores:** `n`, `mean_ns`, `m2_ns` per baseline key.

**Cold start:** Requires `n >= 10` samples before firing. Baseline is meaningful after ~30–50 spans (stddev stabilises).

**False-positive risk:**
- High on right-skewed data. Latency distributions have a long tail; a single slow request drags M2 up. This inflates stddev, making the threshold harder to breach — but it also means the baseline absorbs outliers, so a sustained regression can take many windows to surface.
- Conversely: if traffic is bursty (very low stddev on a cache-hit path), a single slow window fires at 3σ for what is actually a minor blip.

**Drift handling:** Poor. Welford accumulates all history with equal weight. A service that gets 10× slower over six months just keeps shifting the mean upward — the old mean is dragged toward the new normal. This is correct behaviour for gradual drift but means a slow regression may never breach the threshold.

**Explainability:** Good. Alert says: "avg 94ms vs 8ms baseline (11.2σ)". Human-readable.

**Implementation cost:** Already implemented. No new deps, no model storage, no training step.

**Verdict:** Solid for sudden regressions on well-behaved (roughly symmetric) latency paths. Weak on right-skewed distributions and slow drift.

---

### 2. Percentile / IQR

**Mechanism:** Store a rolling sketch of recent durations. At detection time, compute P95 (or P99, or IQR). Flag if the window average exceeds the historical P95.

**What needs to be stored:** A sorted reservoir of the last N durations per baseline key, or a digest structure (e.g. T-Digest) that approximates percentiles from a compact summary. Neither is a single number — it is a variable-length blob.

**Cold start:** Same as Welford — needs enough samples to fill the reservoir (typically N=100–1000 for stable percentile estimates).

**False-positive risk:**
- Lower than Welford on right-skewed data. P95 naturally ignores the tail; if the top 5% of requests are always slow, the P95 threshold already accounts for that. Flagging fires only when the *typical* window average crosses into what used to be the 95th-percentile zone.
- More intuitive to tune: "fire if avg > historical P95" is easier to reason about than "fire if avg > mean + 3σ".

**Drift handling:** Better than Welford if the reservoir is rolling (evict oldest samples). The percentile adapts to the recent distribution automatically as old samples age out.

**Explainability:** Good. "avg 94ms exceeds historical P95 of 32ms."

**Implementation cost:** Medium. Requires storing a sketch per baseline key (a sorted list or T-Digest blob). A reservoir of 200 samples per key at 8 bytes each = 1.6 KB per key — fine at our scale. Adds a migration (new `duration_sketch` column), a serialisation step at ingest, and a deserialise-then-compute step at detection.

**Verdict:** Better fit for skewed latency distributions than Welford. More storage complexity. No external deps if we use a simple fixed-size reservoir; `tdigest` library if we want a compact approximation.

---

### 3. EWMA (Exponentially Weighted Moving Average)

**Mechanism:** At ingest, update a rolling mean with exponential decay: `ewma = α * duration + (1-α) * ewma`. Also track exponentially weighted variance: `ewma_var = (1-α) * (ewma_var + α * (duration - ewma)²)`. Flag if `avg_window > ewma + k * sqrt(ewma_var)`.

**What needs to be stored:** Two extra floats per baseline key: `ewma_ns`, `ewma_var_ns`. Very cheap.

**Cold start:** Near-zero. EWMA bootstraps from the first sample. With `α = 0.1` it is fairly stable after ~30 samples. The first few windows may fire spuriously until the mean settles — solvable with the same `n >= 10` guard already in place.

**False-positive risk:**
- Better than plain Welford for slowly drifting baselines because recent samples carry more weight.
- Still sensitive to right-skewed distributions for the same reason as Welford: a long-tail event inflates the weighted variance.
- α is a tuning knob: small α (0.05–0.1) = slow adaptation, stable baseline, misses fast drift. Large α (0.3+) = reactive, catches fast drift but noisier.

**Drift handling:** This is EWMA's strongest property. Old data decays exponentially; the baseline follows a gradually shifting service without any explicit window management. A service that gets 20% slower over a week will have its EWMA follow it, so no false positive fires on the slow drift itself — only a sudden step change triggers an alert.

**Explainability:** Decent. "avg 94ms vs EWMA baseline 8ms (±3.2ms)." Less intuitive than σ but still human-readable.

**Implementation cost:** Low. Two new columns in `baselines` (`ewma_ns DOUBLE PRECISION`, `ewma_var_ns DOUBLE PRECISION`). One migration. Update logic at ingest is four arithmetic lines. Detection logic is one comparison. No new deps.

**Verdict:** Best fit for the drift-handling requirement. Slightly better than Welford on right-skewed data if α is tuned conservatively. Cheapest upgrade path from the current implementation.

---

### 4. RCF (Random Cut Forest)

**Mechanism:** An ensemble of binary space-partitioning trees. Each new point is scored by how much the forest's structure would change if it were removed — high anomaly score = unusual point. Works on multivariate input; can consume `(duration_ns, error_rate, throughput)` as a single vector.

**What needs to be stored:** A serialised model blob per `detection_config` row — typically 100–500 KB per forest. Stored in a new `detection_models` table.

**Cold start:** Significant. RCF needs a training window — typically 200–2000 historical data points — before the forest is meaningful. During training, no detection fires. This means a newly onboarded tenant is blind for however long it takes to accumulate training data.

**False-positive risk:** Lower than σ-based methods on non-normal data. RCF makes no distribution assumption. However, it is sensitive to the training window — if the training period itself contained anomalies, the model learns them as normal.

**Drift handling:** Requires periodic retraining or use of a streaming variant (e.g. Robust RCF / RRCF). Static RCF becomes stale as the baseline drifts. Streaming RCF is more complex to implement correctly.

**Explainability:** Weak. "Anomaly score: 0.87." No natural explanation of which dimension caused the score.

**Implementation cost:** High.
- New `detection_models` table with `model_blob` (BYTEA).
- Training job: query last N days of spans, fit, serialise to DB. Must re-run on schedule.
- Scoring: deserialise model, score each aggregate vector per tick.
- Library: `rrcf` (Python, pure, small) or `sklearn`'s Isolation Forest as a proxy.
- More moving parts = more failure modes.

**Verdict:** Overkill for nutsafe's current signal set. The signals are not multivariate in a complex sense — each span key is already isolated. The main advantage (no distribution assumption) is achievable more cheaply with Percentile/IQR. Add RCF only when signals become genuinely multivariate and simpler methods demonstrably fail.

---

## Evaluation matrix

| Criterion | Welford+σ | Percentile/IQR | EWMA | RCF |
|---|---|---|---|---|
| Cold-start | 10 samples | 50–100 samples | ~10 samples | 200–2000 samples |
| Right-skewed latency | Weak | Good | Moderate | Good |
| Slow drift handling | Weak | Moderate (rolling) | Strong | Requires retraining |
| False-positive rate | Moderate | Lower | Moderate | Low |
| Explainability | Good | Good | Good | Weak |
| Implementation cost | None (done) | Medium | Low | High |
| New deps | None | None (reservoir) / `tdigest` | None | `rrcf` |
| New DB columns | None | 1 blob column | 2 float columns | 1 model table |

---

## Recommendation

**Primary: EWMA for latency detection, replacing the Welford σ check.**

Rationale:
1. Drift handling is the most practical gap in the current implementation. A service that gradually slows down should not cause a permanent alert — EWMA's decay solves this with two floats and four arithmetic operations.
2. Implementation cost is the lowest of the non-current options: one migration, ~10 lines of changed code, no new dependencies.
3. Explainability remains good; the alert format can stay nearly identical.
4. The cold-start story is unchanged — the existing `n >= 10` guard applies to EWMA too.

**Keep Welford as the baseline store** (we still need `mean_ns` and `m2_ns` for other uses, and removing them would be a larger change). Add `ewma_ns` and `ewma_var_ns` alongside. The detection step switches to reading EWMA state instead of Welford state.

**Keep all other rules unchanged:**
- Error rate: static threshold (10%) is correct — no distribution assumption needed.
- Throughput collapse: ratio check is correct — no distribution assumption needed.
- Cascade: compositional rule, unchanged.
- Resource metrics: static thresholds, unchanged.

**Defer Percentile/IQR** — it is a better fit for skewed distributions but requires blob storage and serialisation complexity. Revisit after EWMA is in production and we have real false-positive data.

**Defer RCF** — no justification at current scale or signal complexity.

---

## α tuning guidance for Phase 4

The EWMA decay factor α controls how quickly the baseline adapts:

| α | Half-life (approx spans) | Use case |
|---|---|---|
| 0.05 | ~14 spans | Stable service, want noise suppression |
| 0.10 | ~7 spans | Default starting point |
| 0.20 | ~3 spans | Fast-changing service, catch regressions quickly |

Start with `α = 0.10`. Expose it as a configurable column in `detection_configs` if per-tenant tuning is needed later.

Detection threshold: `k = 3.0` (same as current σ multiplier). Lower to 2.5 if real-world data shows it misses obvious regressions; raise to 3.5 if false positives are frequent.
