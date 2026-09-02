F1 LAP-TIME ANALYSIS — BOTTLENECK RESOLUTION PLAN

PURPOSE
-------
This document defines the solution to the two current bottlenecks:

1. Fuel coefficient identification
2. Negative tyre-degradation curves

The goal is to establish a defensible production methodology now and
define the data/model improvements required later.


1. FUEL COEFFICIENT
===================

PROBLEM
-------
The current fuel_load_kg is computed as:

    fuel_load_kg = 100 - (lap - 1) * 100 / total_laps

Therefore fuel mass is a deterministic linear function of race lap.

Within a race:

    corr(fuel_load_kg, lap) = -1

This means race data cannot independently identify the fuel effect from
general lap-number/race-evolution effects.

Previous attempts to fit the coefficient from race data should therefore
NOT be used as the production estimator.

DECISION: DO NOT FIT THE FUEL COEFFICIENT FROM CURRENT RACE DATA
---------------------------------------------------------------

Use the fixed published reference:

    fuel_coefficient = 0.030 seconds / kg / lap

Published F1 modelling commonly uses approximately 0.030–0.040 s/kg/lap
as a first-order fuel sensitivity. The 0.030 value is therefore a
reasonable reference prior, not a coefficient learned from this dataset.

Store it explicitly as:

    fuel_coefficient = 0.030
    fuel_coefficient_source = "published_default"
    fuel_coefficient_fitted = False

Do NOT claim that 0.030 was statistically estimated from race data.


FUEL CORRECTION
---------------
Estimate fuel mass using the existing race-distance approximation:

    fuel_mass(lap) =
        fuel_start - fuel_burn_per_lap * (lap - 1)

Then:

    fuel_penalty =
        fuel_mass * fuel_coefficient

And:

    fuel_corrected_lap_time =
        raw_lap_time - fuel_penalty

This is an estimated normalization, not measured fuel telemetry.


WHY THE PREVIOUS REGRESSIONS FAILED
------------------------------------
A regression can return a coefficient that looks reasonable and has a
high R² while still being statistically unidentified.

The decisive evidence is the instability across subsets and the fact
that within a stint:

    corr(fuel, tyre_age) = -1.0000

Fuel and tyre age both progress deterministically with lap number.
Therefore the regression cannot reliably separate the two effects.


FUTURE SOLUTION FOR FUEL COEFFICIENT
------------------------------------
Do NOT run another race-only regression.

Add practice-session long-run ingestion.

The required data should include:

    session
    driver
    compound
    tyre_age
    lap_time
    estimated_fuel_mass
    track_condition
    track_status

The critical requirement is genuine independent variation in fuel mass
at comparable tyre ages.

Ideal data:

    same driver
    same compound
    similar tyre age
    different fuel loads
    clean green-flag laps

Then a future model can estimate:

    LapTime =
        baseline
        + beta_fuel * FuelMass
        + degradation_terms
        + track_terms
        + error

Only replace 0.030 with a fitted coefficient after:

    1. Stable estimates across practice sessions
    2. Stable estimates across circuits
    3. Stable estimates across tyre subsets
    4. Reasonable confidence intervals
    5. Successful out-of-sample validation
    6. No major sensitivity to model specification

Until then:

    KEEP 0.030.


2. NEGATIVE TYRE-DEGRADATION CURVES
===================================

PROBLEM
-------
Some fitted degradation slopes are negative.

Current observed counts:

    2022: 7 / 50 curves negative
    2023: 3 / 48 curves negative
    Total: 10 / 98

A negative fitted slope does not necessarily mean the tyre physically
improves with age.

The main issue is that short stints do not contain enough tyre-age range
to identify a reliable degradation slope.

Tyre performance can follow:

    warm-up -> peak performance -> degradation

A short 6–8 lap stint can therefore sit mostly around the flat/peak
region. A straight-line fit through that region can produce a negative
slope even when the underlying long-run degradation is positive.


CURRENT PRODUCTION SOLUTION
---------------------------
Keep the warm-up cutoff already implemented.

Then:

    1. Fit the degradation slope.
    2. Validate whether it is physically plausible.
    3. If slope < 0:
           is_physical = False
    4. Clamp the value to zero for downstream optimisation/simulation.
    5. Preserve the original fitted value for diagnostics.

Conceptually:

    fitted_degradation < 0
            |
            v
    is_physical = False
            |
            v
    downstream_degradation = max(0, fitted_degradation)

Do NOT silently delete negative curves.

The negative estimate should remain visible as evidence that the stint
did not provide a reliable positive degradation estimate.


WHY NOT REQUIRE VERY LONG STINTS?
---------------------------------
Increasing the minimum number of post-warm-up laps would reduce unstable
fits, but it would also remove legitimate short stints and may introduce
selection bias.

Therefore use:

    warm-up cutoff
    +
    physical validation
    +
    is_physical flag
    +
    zero clamp for downstream use

This is the preferred current solution.


RECOMMENDED DEGRADATION METADATA
--------------------------------
Retain:

    stint_length
    usable_laps
    tyre_age_range
    fitted_slope
    is_physical
    fit_quality
    excluded_lap_count

This makes every degradation estimate auditable.


FUTURE SOLUTION: STATE-SPACE MODEL
----------------------------------
The long-term solution is a latent-state/state-space model rather than
a simple linear degradation slope.

Conceptually:

    observed_lap_time_t =
        latent_tyre_pace_t
        + fuel_effect
        + observation_error

and:

    latent_tyre_pace_(t+1) =
        latent_tyre_pace_t
        + degradation_process
        + process_noise

Pit stops can reset the latent tyre state.

This can represent:

    warm-up
    peak performance
    gradual degradation
    changing degradation rate
    noisy laps
    driver/traffic errors

A state-space approach has specifically been proposed for F1 tyre
degradation using FastF1 timing data (arXiv:2512.00640).


3. PRODUCTION PIPELINE TO FOLLOW NOW
====================================

                    RAW LAP DATA
                         |
                         v
                 DATA VALIDATION
                         |
                         v
                REMOVE BAD LAPS
                         |
              +----------+----------+
              |                     |
              v                     v
        FUEL CORRECTION       TYRE ANALYSIS
              |                     |
      fixed 0.030 s/kg        warm-up cutoff
              |                     |
              |              fit degradation
              |                     |
              |              physical validation
              |                     |
              |              negative -> flag
              |                     |
              |              downstream -> max(0,x)
              |                     |
              +----------+----------+
                         |
                         v
                 CORRECTED LAP TIME
                         |
                         v
                 PERFORMANCE ANALYSIS


4. WHAT NOT TO DO
=================

DO NOT:

- Fit fuel coefficient from race lap number alone.
- Treat derived fuel_load_kg as independent information.
- Trust a high R² as proof that fuel coefficient is identified.
- Select a coefficient simply because it falls in the published range.
- Remove negative degradation curves silently.
- Force every short stint to produce a reliable degradation slope.
- Replace 0.030 until independent fuel variation is available.


5. CODE CONFIGURATION
=====================

Fuel:

    FUEL_COEFFICIENT = 0.030
    FUEL_COEFFICIENT_SOURCE = "published_default"
    FUEL_COEFFICIENT_FITTED = False

Calculation:

    fuel_mass = fuel_start - fuel_burn_per_lap * (lap - 1)

    fuel_penalty = fuel_mass * FUEL_COEFFICIENT

    fuel_corrected_lap_time = lap_time - fuel_penalty

Degradation validation:

    fitted_deg = regression_slope

    if fitted_deg < 0:
        is_physical = False
        downstream_deg = 0.0
    else:
        is_physical = True
        downstream_deg = fitted_deg


6. ACCEPTANCE CRITERIA
======================

FUEL MODEL
----------
PASS when:

- 0.030 is explicitly documented as a published prior/reference.
- No race-only fuel fitting is used.
- Source and method are stored in metadata.
- Fuel correction reduces the expected race-wide fuel trend.
- The coefficient is not presented as dataset-estimated.

FUTURE FUEL MODEL
-----------------
Only accept a fitted coefficient if:

- independent fuel variation exists,
- coefficient is stable across subsets,
- confidence intervals are reasonable,
- cross-session validation succeeds,
- cross-circuit validation succeeds.


DEGRADATION MODEL
-----------------
PASS when:

- warm-up laps are handled,
- negative slopes are flagged,
- negative slopes are not silently discarded,
- downstream optimisation cannot use negative degradation,
- fit-quality information remains available.


7. DEVELOPMENT PRIORITY
=======================

Priority 1:
    Add practice-session ingestion.

Priority 2:
    Build a clean practice long-run dataset.

Priority 3:
    Estimate fuel coefficient using genuine independent fuel variation.

Priority 4:
    Validate the fitted coefficient against the 0.030 reference.

Priority 5:
    Replace simple linear tyre degradation with a state-space model.

Do NOT make the state-space model a blocker for the current pipeline.


FINAL DECISION
==============

FUEL
----
Use:

    0.030 s/kg/lap

as a fixed published reference.

Do not fit it from the current race dataset.

The current race dataset cannot identify the coefficient because fuel mass
is constructed directly from lap number.

The correct future solution is practice-session long-run data with
independent fuel variation.


TYRE DEGRADATION
----------------
Keep the current linear estimator for now.

Keep the warm-up cutoff.

For negative estimates:

    is_physical = False
    downstream_degradation = 0

Do not hide the original estimate.

The long-term solution is a state-space/latent-state degradation model.


BOTTOM LINE
===========
The current system should NOT be blocked by either issue.

Fuel:
    fixed published prior now,
    practice-data estimator later.

Tyre degradation:
    validated linear estimator now,
    state-space model later.

This provides a transparent, reproducible, and defensible methodology
without pretending that unavailable information can be statistically
recovered from collinear race data.
