# Models and training

What is fitted, what it is fitted on, and what each result is worth.

## The short version

Most of this engine is not machine learning. It is regression and simulation, chosen
deliberately: the questions are physical — how much does a lap of tyre age cost, what
does a pit stop lose — and a fitted line answers them in units a race engineer can
argue with. A gradient-boosted model that predicted lap time to 0.05 s would answer none
of them.

There is exactly one model in the usual sense — a ridge regression predicting finishing
position — and the honest summary is that it barely beats predicting the grid, and its
own quality gate does not reliably detect that. Details in
[Outcome prediction](#outcome-prediction), including the measurement that shows the gate
failing.

| What | Method | Fitted per | Where it runs | In the product |
|---|---|---|---|---|
| [Stint pace and degradation](#stint-regression) | OLS, degree 1 | Stint | `transform`/`analyse` | Yes — everywhere |
| [Degradation curves](#degradation-curves) | Median of stint slopes | Compound × session | `analyse` | Yes |
| [Fuel correction](#fuel-correction) | **Not fitted** — fixed prior | — | `transform` | Yes |
| [Pit loss](#pit-loss) | Quantile of observed excess | Session | `strategy` | Yes |
| [Race simulation](#race-simulation) | Monte Carlo | Per request | `simulate` | Yes |
| [Driver ratings](#driver-ratings) | Fixed weighted sum | Season | `ratings` | Yes |
| [Outcome prediction](#outcome-prediction) | Ridge regression | Season holdout | Library only | **No** |

Nothing here is trained on a GPU, nothing takes longer than a few seconds, and no model
weights are persisted. Every fit is cheap enough to recompute from source, which is why
`engine_version` invalidation works: a changed model means recomputing, not retraining.

## Why so little ML

Three reasons, in order of weight.

**The dataset is small.** 44 races, 900 driver-race results, 2,065 stints. After the
leakage-safe windowing described below, 847 usable samples with six features. That
supports a linear model with regularisation and very little else — a tree ensemble on
847 rows memorises the training seasons.

**The questions are physical, and the answers need units.** "Degradation at Sakhir is
0.143 s/lap" is checkable against a strategy engineer's intuition and against published
research. A model output of "0.62 predicted tyre-life-index" is not. Every number this
engine produces is in seconds, positions, or laps.

**A wrong answer has to be explainable.** When a degradation slope comes back negative,
the fitted line can be inspected: how many laps, over what tyre-age range, with what
residual. That is how the warm-up problem was actually found — see
[fuel-and-degradation-methodology.md](fuel-and-degradation-methodology.md).

## Stint regression

The foundational fit. Everything downstream depends on it.

**Model.** Ordinary least squares, degree 1, in
[`stint_model.py`](../backend/src/f1x/engine/pace/stint_model.py):

```
lap_time_s = pace_s + degradation_s_per_lap × tyre_age
```

Fitted with `np.polyfit(age, times, 1)`. The intercept is the stint's underlying pace;
the slope is what each lap of tyre age costs.

**Training data.** One fit per stint, from that stint's laps only. No pooling — a stint
is the only unit over which the compound, the fuel state and the driver are all constant.

**What is excluded before fitting**, and why each matters:

| Filter | Value | Reason |
|---|---|---|
| `MIN_STINT_LAPS` | 5 | Below this the fit is arithmetic, not evidence |
| `DEGRADATION_ONSET_LAPS` | 4 | Tyres are *fastest* around age 3; fitting from lap 1 fits a curve with a line |
| Distinct tyre ages | ≥ 2 | A slope needs spread, not just points |
| Fuel correction | applied first | Otherwise burn-off shows up as negative degradation |

The warm-up cutoff is the non-obvious one. Measured across ~10,000 laps of 2023, lap
time relative to each stint's own median runs −0.290 s at age 2, −0.310 s at age 3,
−0.277 s at age 4, then rises monotonically. A set comes *up* to temperature before it
degrades. Fitting from age 1 produced negative slopes at Jeddah, Melbourne and Baku;
excluding the warm-up laps cut negative fits from 10/48 to 3/43 at the time.

**Two pace numbers, and they are not interchangeable.** `pace_s` is the fitted
intercept at tyre age zero. Because the fit starts at `DEGRADATION_ONSET_LAPS`, that is
an *extrapolation* — nothing at age zero was observed, and a stint is back-cast in
proportion to its own slope. `reference_pace_s` evaluates the same line at the onset
age, the youngest point actually seen.

Use the intercept to recover a generating line; use the reference for any comparison.
Pooling intercepts across compounds measured a soft-to-medium gap of 0.272 s where the
unextrapolated figure is 0.189 s — the back-cast, which is larger for the
faster-degrading compound, was inflating the gap by 30%.

**Validation.** `MAX_PLAUSIBLE_DEG_S_PER_LAP = 0.22` s/lap, the upper bound from Kolbe
et al. (arXiv:2607.06495), fitted across 8,278 stints and 191k laps. A slope beyond it
is a damaged car or a mislabelled stint, not tyre wear, and is flagged unphysical rather
than silently used.

**Measured on the current database** (2,065 stints):

| | |
|---|---|
| Fits inside [0, 0.22] s/lap | 1,593 (77%) |
| Negative slopes | 323 (16%) |
| Mean r² | 0.457 |

The mean r² is low and that is expected, not a defect. A stint is not a clean linear run:
traffic, a mistake, or a safety car all add variance the tyre-age term cannot explain.
Low r² means "this stint was messy", not "the driver was slow" — which is why r² is
carried through to the API rather than hidden. The 323 negative fits are preserved for
diagnosis and clamped to zero downstream, so an optimiser can never treat degradation as
a benefit.

## Degradation curves

**Method.** Not a fit — the **median** of the per-stint slopes, grouped by compound
within a session, with the interquartile range carried alongside.

Median rather than mean because one damaged-car stint would drag a mean well outside the
physical band. The IQR travels with it so a curve fitted from three noisy stints is
visibly less certain than one from thirty.

`median_pace_s` pools `reference_pace_s`, not the intercept, for the reason above.
Within-session compound deltas now read HARD +0.250 s and MEDIUM +0.189 s against SOFT
— the physically correct order, and the only comparison that is valid, since pooling
across sessions mixes circuits with different lap times.

100 curves in the current database. The compound ordering that falls out —
INTERMEDIATE 0.162 > SOFT 0.074 > MEDIUM 0.055 > HARD 0.048 s/lap — is **not enforced
anywhere in the code**. It emerges from the fits, which is the useful signal: the model
recovers a physical fact nobody encoded.

## Fuel correction

**This parameter is not fitted. It is a fixed prior of 0.030 s/kg**, and that is a
finding rather than a shortcut.

`fuel_load_kg` is derived from lap number as `100 - (lap - 1) × 100 / total_laps`, so
within any race the two are collinear by construction. Measured correlation: **−1.0000
in 100% of 1,816 stints**. A regression cannot separate a fuel effect from a lap-number
effect when they are the same variable.

Three approaches were tried and all failed:

| Approach | Result |
|---|---|
| Per-circuit | ~1.0 s/kg — thirty times physical; the fuel term absorbed every trend |
| Cross-season pooling | Monza correlation still −0.9993; 17 of 20 circuits ran identical lap counts |
| Pooled + stint fixed effects | +0.049 s/kg at r² 0.996 — then −0.041 on softs, +0.072 on hards |

The third is the instructive failure. It produces a plausible number in the published
0.025–0.040 band, and it is meaningless: fixed effects leave only within-stint variation,
where fuel and tyre age are perfectly collinear, so the solver splits their shared effect
arbitrarily and the split moves with whatever else differs between subsets.

Fitting this properly needs fuel that varies independently of race progress — practice
long-runs, where teams deliberately test different loads at the same tyre age. Practice
ingestion is not built. Full working in
[fuel-and-degradation-methodology.md](fuel-and-degradation-methodology.md).

## Pit loss

**Method.** No model. The 25th percentile of observed pit-lap excess over a green-flag
reference lap, per session.

A quantile rather than a mean because pit laps include stops behind a safety car, stops
with a problem, and stops into traffic. The lower quartile approximates a clean stop,
which is what a strategy question is actually about. The IQR is reported so a session
with chaotic stops is visibly noisier.

## Race simulation

**Method.** Monte Carlo, in [`race.py`](../backend/src/f1x/engine/simulation/race.py).
Not trained — it *consumes* the fitted degradation and pit-loss values and samples
outcomes.

Tyre age runs 1..n within a stint, matching `tyre_life` in the data and the optimiser's
`degradation_cost`. All three previously disagreed with each other by one lap of wear
in places; the offset is `slope × total_laps`, identical across strategies, so it never
moved a ranking — but both modules report absolute figures, so it is now consistent.

Per iteration: lap times from the deterministic pace-plus-degradation model, Gaussian
lap noise, a Bernoulli draw for a safety car with a uniformly placed start lap, and
Gaussian noise on each pit loss.

**One detail matters more than the rest.** Every strategy under comparison is simulated
with the *same seed*, so they face the same sampled races. Without it a two-stop could
beat a one-stop because it happened to draw fewer safety cars, and the comparison would
measure the random draw rather than the strategy. `is_decisive` then reports whether the
gap between strategies exceeds the spread within them — a strategy that wins by less
than the noise is reported as a tie.

## Driver ratings

**Method.** A weighted sum of four min-max normalised components. **The weights are a
judgement, not a fit:**

```python
WEIGHTS = {"pace": 0.40, "racecraft": 0.25, "consistency": 0.20, "tyre_management": 0.15}
```

There is no ground-truth "driver quality" to regress against, so nothing here is
learnable — any weighting is an opinion about what matters. They are stated in the code
and in the API response so a reader can disagree with a specific number rather than with
a black box, and `build_ratings` accepts a `weights` override for exactly that reason.

The components are shown alongside the total rather than behind it, because they disagree
often enough that the disagreement is the interesting part — the quickest driver is
frequently not the best at managing tyres.

## Outcome prediction

The one model in the conventional sense. **It is not wired into the CLI or the API** —
it exists as a library with tests, and nothing in the product calls it. Read what follows
as an evaluation of whether it *should* be, and the answer is currently no.

**Model.** Ridge regression, closed form, in
[`model.py`](../backend/src/f1x/engine/predictive/model.py):

```python
gram = augmented.T @ augmented + penalty
return np.linalg.solve(gram, augmented.T @ target)
```

`RIDGE_ALPHA = 1.0`. The intercept column is excluded from the penalty — shrinking it
would bias every prediction. Written out rather than imported from scikit-learn: it is
four lines, it makes the regularisation explicit, and it keeps the engine dependency-free.

**Target.** Finishing position.

**Features** — six, all built strictly from a driver's *prior* races:

| Feature | Meaning |
|---|---|
| `grid_position` | Where they started |
| `prior_mean_finish` | Rolling mean finish, previous 5 races |
| `prior_best_finish` | Rolling best finish, previous 5 |
| `prior_mean_pace_gap` | Rolling mean fuel-corrected pace gap |
| `prior_finish_rate` | Share of prior races finished — a reliability proxy |
| `prior_races` | How much history exists |

### How leakage is prevented

This is the part that matters most, because a leak is invisible in the metrics — it
makes the score better.

**Every rolling aggregate is shifted by one race** (`.shift(1)` before
`.rolling_mean()`, partitioned by driver), so a row never sees its own result. A model
fed a driver's season-wide average finish would predict that season nearly perfectly and
predict nothing about a race it had not seen.

**Drivers with fewer than `MIN_PRIOR_RACES = 2` are dropped, not imputed.** Filling a
debut race with a season average would inject the future into the past.

**The train/test split is on a season boundary**, never random. A random split lets a
model learn from a race and be tested on the one before it. Holding out a whole season
answers the question actually being asked: given what we knew, would this have predicted
what followed?

**`leakage_check()` runs independently** and flags any feature correlating above 0.98
with the target. It has fired correctly on degenerate test fixtures, which is how the
fixtures got fixed.

### Measured results

Trained on 2022, tested on 2023, from the live database:

| | Ridge | Baseline (predict the grid) |
|---|---|---|
| MAE | **3.42 places** | 3.82 places |
| Spearman | **0.637** | 0.566 |
| Samples | 416 train / 431 test | — |

847 usable samples from 900 raw results; `leakage_check` clean. Improvement 10.3%, and
the model's own verdict string is *"marginally better than grid position"*.

Standardised coefficients: `prior_mean_finish` +1.250, `grid_position` +1.196,
`prior_mean_pace_gap` +1.111, `prior_best_finish` +0.460, `prior_finish_rate` −0.335,
`prior_races` +0.074. These say what the model leans on. They are **not** causal — the
inputs are correlated with each other, so a coefficient does not isolate an effect.

### The baseline is the point

Grid position alone explains most of the variance in a modern Formula 1 race. Any model
of finishing position competes against *predict everyone finishes where they started*,
and one that cannot beat it has learned nothing. So the baseline is reported alongside
every score, and `beats_baseline` requires both a lower MAE **and** a higher rank
correlation.

That second condition exists because MAE alone is not enough: when finishing order is
close to random, predicting the middle of the field beats predicting grid position on
MAE while getting the ranking backwards. On pure noise a ridge fit once scored a 16.5%
MAE "improvement" with a rank correlation of −0.12.

### Where this gate is still too weak

**The rank-correlation condition does not reliably catch noise.** Measured directly:
generate results with a *random* finishing order, 22 races × 20 drivers × 2 seasons, and
evaluate across 30 seeds.

**The model was declared better than the baseline in 17 of 30 noise trials** — roughly a
coin flip. One representative seed reports MAE 5.21 vs 6.75, a 22.8% "improvement", and a
verdict of *"clearly better than grid position"*, on data with no signal in it at all.

The reason is that `beats_baseline` compares two rank correlations without asking whether
either is distinguishable from zero. On random data both are near zero (−0.028 vs −0.032
in that seed), and whichever lands higher is a coin toss. The MAE condition then passes
easily, because predicting the field's middle genuinely beats predicting the grid when
the grid carries no information.

So the 10.3% improvement on real data should be read with that in mind. The real-data
rank correlations (0.637 vs 0.566) are far from zero and clearly ordered, which is
better evidence than the MAE gap — but the gate that is supposed to certify this would
have passed on noise too, so it is not the gate that makes the result credible.

Fixing it needs a significance test on the rank-correlation *difference*, or a permutation
baseline: shuffle the target, refit, and require the real model to beat the shuffled
distribution rather than a single alternative. Neither is implemented. **This is the main
reason the module is not wired into the product**, and it should stay unwired until the
gate is strengthened.

## Reproducing these numbers

The stint and degradation figures come from the pipeline:

```bash
.venv/Scripts/python.exe -m f1x.cli analyse all
.venv/Scripts/python.exe -m f1x.cli analyse quality    # leakage and fit health
.venv/Scripts/python.exe -m f1x.cli analyse fuel       # the fuel-fit diagnostics
```

The prediction figures have no CLI entry point, which is the point made above. They were
produced by calling the library directly against the live database — joining
`core.results` to `core.entries` for `driver_number` and to `mart.pace_rankings` for the
pace gap, then `build_features` → `leakage_check` → `evaluate(holdout_season=2023)`.

Model behaviour is covered by [`test_model.py`](../tests/unit/test_model.py) and
[`test_features.py`](../tests/unit/test_features.py). The tests that matter there are the
negative ones — a model fed noise must be reported as no better than the baseline. As
measured above, that test passes on the fixtures used while the property does not hold in
general, which is worth knowing about the test as much as about the model.

## Related

- [methodology.md](methodology.md) — how each figure is derived and what it claims
- [fuel-and-degradation-methodology.md](fuel-and-degradation-methodology.md) — the two
  modelling limits in full
- [architecture.md](architecture.md) — where each layer sits
