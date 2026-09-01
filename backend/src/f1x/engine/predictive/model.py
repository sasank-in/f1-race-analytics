"""Race outcome prediction, with the baseline that has to be beaten.

Any model of finishing position is competing against a very strong trivial answer:
*predict that everyone finishes where they started*. Grid position alone explains most
of the variance in a modern Formula 1 race, and a model that cannot beat it has learned
nothing worth having.

So the baseline is not an afterthought here — it is reported alongside every model, and
a model that fails to beat it is reported as having failed. That is a more useful thing
to know than an impressive-looking error figure with nothing to compare it against.

Metrics are mean absolute error in positions (interpretable: "wrong by 2.3 places") and
Spearman correlation of predicted against actual order (does it get the *ranking* right,
which is what a championship cares about).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import polars as pl

from f1x.engine.predictive.features import FeatureSet

# Ridge regularisation. With a few hundred samples and correlated features, some
# shrinkage is necessary; the exact value matters little between 0.1 and 10.
RIDGE_ALPHA = 1.0


@dataclass(frozen=True)
class ModelScore:
    """How a model performed against held-out races."""

    name: str
    n_train: int
    n_test: int

    # Mean absolute error in finishing positions.
    mae: float
    # Rank correlation between predicted and actual finishing order.
    spearman: float

    @property
    def summary(self) -> str:
        return f"{self.name}: MAE {self.mae:.2f} places, rank correlation {self.spearman:.3f}"


@dataclass(frozen=True)
class Evaluation:
    """A model and the baseline it has to beat."""

    model: ModelScore
    baseline: ModelScore

    @property
    def beats_baseline(self) -> bool:
        """Whether the model is actually worth using.

        Two conditions, and the second is the one that matters. A lower mean absolute
        error is *not* sufficient: when finishing order is close to random, predicting
        the middle of the field beats predicting grid position on MAE while getting
        the ranking backwards. Measured on pure noise, a ridge fit scored a 16.5 %
        MAE "improvement" with a rank correlation of -0.12.

        So the model must also order the field better than the baseline does. Error
        magnitude says how far off it is; rank correlation says whether it understood
        anything.
        """
        return (
            self.model.mae < self.baseline.mae - 0.05
            and self.model.spearman > self.baseline.spearman
        )

    @property
    def improvement(self) -> float:
        """Reduction in mean absolute error, as a fraction of the baseline's."""
        if self.baseline.mae == 0:
            return 0.0
        return (self.baseline.mae - self.model.mae) / self.baseline.mae

    @property
    def verdict(self) -> str:
        if self.model.spearman <= self.baseline.spearman:
            return "orders the field no better than grid position"
        if not self.beats_baseline:
            return "no better than grid position"
        if self.improvement > 0.15:
            return "clearly better than grid position"
        return "marginally better than grid position"


def _spearman(predicted: np.ndarray, actual: np.ndarray) -> float:
    """Rank correlation, computed without a scipy dependency at call time."""
    if predicted.size < 2:
        return 0.0
    predicted_ranks = np.argsort(np.argsort(predicted)).astype(float)
    actual_ranks = np.argsort(np.argsort(actual)).astype(float)
    if predicted_ranks.std() == 0 or actual_ranks.std() == 0:
        return 0.0
    return float(np.corrcoef(predicted_ranks, actual_ranks)[0, 1])


def _score(
    name: str, predicted: np.ndarray, actual: np.ndarray, n_train: int
) -> ModelScore:
    return ModelScore(
        name=name,
        n_train=n_train,
        n_test=int(actual.size),
        mae=float(np.mean(np.abs(predicted - actual))),
        spearman=_spearman(predicted, actual),
    )


def fit_ridge(
    design: np.ndarray, target: np.ndarray, *, alpha: float = RIDGE_ALPHA
) -> np.ndarray:
    """Closed-form ridge regression, including an intercept column.

    Written out rather than pulled from scikit-learn: it is four lines, it makes the
    regularisation explicit, and it keeps the model layer dependency-free.
    """
    n_features = design.shape[1]
    penalty = alpha * np.eye(n_features + 1)
    # The intercept is not penalised — shrinking it would bias every prediction.
    penalty[0, 0] = 0.0
    augmented = np.column_stack([np.ones(design.shape[0]), design])
    gram = augmented.T @ augmented + penalty
    return np.linalg.solve(gram, augmented.T @ target)


def predict(coefficients: np.ndarray, design: np.ndarray) -> np.ndarray:
    augmented = np.column_stack([np.ones(design.shape[0]), design])
    return augmented @ coefficients  # type: ignore[no-any-return]


def evaluate(features: FeatureSet, *, holdout_season: int) -> Evaluation | None:
    """Train on prior seasons, test on a held-out one, and compare to the baseline."""
    train, test = features.split_by_season(holdout_season)
    if train.is_empty() or test.is_empty():
        return None

    columns = [c for c in features.feature_columns if c in train.columns]
    if not columns:
        return None

    train_clean = train.drop_nulls([*columns, features.target_column])
    test_clean = test.drop_nulls([*columns, features.target_column])
    if train_clean.is_empty() or test_clean.is_empty():
        return None

    design = train_clean.select(columns).to_numpy().astype(float)
    target = train_clean.get_column(features.target_column).to_numpy().astype(float)
    test_design = test_clean.select(columns).to_numpy().astype(float)
    test_target = test_clean.get_column(features.target_column).to_numpy().astype(float)

    coefficients = fit_ridge(design, target)
    model = _score("ridge", predict(coefficients, test_design), test_target, len(train_clean))

    # The baseline: predict that every driver finishes where they qualified.
    grid = test_clean.get_column("grid_position").to_numpy().astype(float)
    baseline = _score("grid position", grid, test_target, 0)

    return Evaluation(model=model, baseline=baseline)


def feature_importance(
    features: FeatureSet, *, holdout_season: int
) -> list[tuple[str, float]]:
    """Standardised coefficients, so features on different scales are comparable.

    Not a causal claim. These say what the model leans on, which is worth knowing when
    a prediction looks wrong, but a coefficient on correlated inputs does not isolate
    an effect.
    """
    train, _ = features.split_by_season(holdout_season)
    columns = [c for c in features.feature_columns if c in train.columns]
    clean = train.drop_nulls([*columns, features.target_column])
    if clean.is_empty() or not columns:
        return []

    design = clean.select(columns).to_numpy().astype(float)
    target = clean.get_column(features.target_column).to_numpy().astype(float)

    # Standardise so a coefficient on grid position (1-20) is comparable with one on
    # pace gap (0-2 seconds).
    spread = design.std(axis=0)
    spread[spread == 0] = 1.0
    standardised = (design - design.mean(axis=0)) / spread

    coefficients = fit_ridge(standardised, target)[1:]
    return sorted(
        zip(columns, (float(c) for c in coefficients), strict=True),
        key=lambda pair: -abs(pair[1]),
    )


def build_results_frame(rows: list[dict[str, object]]) -> pl.DataFrame:
    """Assemble the raw result rows the feature store expects."""
    return pl.DataFrame(rows) if rows else pl.DataFrame()
