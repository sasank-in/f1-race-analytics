"""Race insight rules.

The rules exist to say what is *unusual* about a race, so the tests that matter are the
negative ones: an ordinary race must produce an ordinary summary rather than a
manufactured drama. A rule that fires on everything is worth nothing.
"""

from __future__ import annotations

from dataclasses import dataclass

from f1x.engine.insights import (
    BIG_PLACE_SWING,
    CLOSE_FIELD_S,
    DOMINANT_GAP_S,
    HIGH_ATTRITION,
    HIGH_DEGRADATION_S,
    build_insights,
)


@dataclass
class Row:
    """Stands in for a pace ranking row."""

    driver_number: str
    rank: int
    gap_to_best_s: float
    abbreviation: str | None = None
    finish_position: float | None = None
    did_not_finish: bool = False
    std_s: float = 0.2


def _field(gaps: list[float], **overrides: object) -> list[Row]:
    """A field where everyone finished exactly where their pace ranked them."""
    rows = []
    for i, gap in enumerate(gaps, start=1):
        rows.append(
            Row(
                driver_number=str(i),
                rank=i,
                gap_to_best_s=gap,
                abbreviation=f"D{i:02d}",
                finish_position=float(i),
            )
        )
    for key, value in overrides.items():
        setattr(rows[0], key, value)
    return rows


def kinds(insights: list[object]) -> list[str]:
    return [i.kind for i in insights]  # type: ignore[attr-defined]


def test_no_pace_data_yields_nothing() -> None:
    """Better an empty summary than one invented from no evidence."""
    assert build_insights([]) == []


def test_an_ordinary_race_produces_no_drama() -> None:
    """The quickest car won, the field was normally spread, everyone finished."""
    field = _field([0.0, 0.2, 0.35, 0.5])
    found = build_insights(field, winner_number="1", n_retirements=1)
    assert "upset" not in kinds(found)
    assert "attrition" not in kinds(found)
    assert "close_field" not in kinds(found)


def test_the_quickest_car_not_winning_is_the_headline() -> None:
    """The premise of the whole application, so it must come first."""
    field = _field([0.0, 0.2, 0.4])
    found = build_insights(field, winner_number="2")
    assert found[0].kind == "upset"
    assert "did not win" in found[0].headline
    # The winner's own pace deficit is the supporting number.
    assert found[0].magnitude == 0.2


def test_a_quick_car_retiring_is_reported() -> None:
    field = _field([0.0, 0.2, 0.4])
    field[2].did_not_finish = True
    field[2].finish_position = None

    found = build_insights(field, winner_number="1")
    retirement = next(i for i in found if i.kind == "attrition")
    assert "P3 on pace and retired" in retirement.headline


def test_a_midfield_retirement_is_not_reported() -> None:
    """Only a quick car retiring changes how the result should be read."""
    field = _field([0.0, 0.2, 0.4, 0.6, 0.8, 1.0])
    field[5].did_not_finish = True
    field[5].finish_position = None

    found = build_insights(field, winner_number="1")
    assert not any("retired" in i.headline for i in found)


def test_a_finish_far_below_the_pace_is_reported() -> None:
    field = _field([0.0, 0.2, 0.4, 0.6])
    field[3].finish_position = 4.0 + BIG_PLACE_SWING

    found = build_insights(field, winner_number="1")
    swing = next(i for i in found if "with the P4 car" in i.headline)
    assert swing.magnitude == BIG_PLACE_SWING


def test_a_small_swing_is_not_reported() -> None:
    """Finishing a place or two off the pace is a normal race, not a finding."""
    field = _field([0.0, 0.2, 0.4, 0.6])
    field[3].finish_position = 4.0 + BIG_PLACE_SWING - 1

    found = build_insights(field, winner_number="1")
    assert not any("with the P4 car" in i.headline for i in found)


def test_a_dominant_car_is_reported() -> None:
    field = _field([0.0, DOMINANT_GAP_S + 0.05, 0.6])
    found = build_insights(field, winner_number="1")
    assert "dominance" in kinds(found)


def test_a_close_front_row_is_reported() -> None:
    field = _field([0.0, CLOSE_FIELD_S - 0.01, 0.6])
    found = build_insights(field, winner_number="1")
    close = next(i for i in found if i.kind == "close_field")
    assert "separated by" in close.headline


def test_dominance_and_closeness_are_mutually_exclusive() -> None:
    """They are opposite claims about the same number and must never co-occur."""
    for margin in (0.0, 0.05, 0.1, 0.2, 0.35, 0.5, 1.0):
        found = kinds(build_insights(_field([0.0, margin, 1.5]), winner_number="1"))
        assert not ("dominance" in found and "close_field" in found)


def test_high_degradation_is_reported_with_its_compound() -> None:
    field = _field([0.0, 0.2])
    found = build_insights(
        field,
        winner_number="1",
        degradation_s_per_lap=HIGH_DEGRADATION_S + 0.02,
        worst_compound="SOFT",
    )
    deg = next(i for i in found if i.kind == "degradation")
    assert "SOFT" in deg.headline
    # The caveat has to travel with the number: this is fitted, not measured.
    assert deg.detail is not None and "Fitted" in deg.detail


def test_ordinary_degradation_is_not_reported() -> None:
    found = build_insights(
        _field([0.0, 0.2]),
        winner_number="1",
        degradation_s_per_lap=HIGH_DEGRADATION_S - 0.02,
    )
    assert "degradation" not in kinds(found)


def test_high_attrition_is_reported() -> None:
    found = build_insights(
        _field([0.0, 0.2]), winner_number="1", n_retirements=HIGH_ATTRITION
    )
    assert "attrition" in kinds(found)


def test_a_normal_number_of_retirements_is_not_reported() -> None:
    found = build_insights(
        _field([0.0, 0.2]), winner_number="1", n_retirements=HIGH_ATTRITION - 1
    )
    assert not any("did not finish" in i.headline for i in found)


def test_stop_count_reads_as_english() -> None:
    """"3-stops race" is the kind of wording that makes a page look generated."""
    one = build_insights(_field([0.0, 0.2]), winner_number="1", optimal_stops=1)
    three = build_insights(_field([0.0, 0.2]), winner_number="1", optimal_stops=3)
    assert "1-stop race" in next(i for i in one if i.kind == "strategy").headline
    assert "3-stop race" in next(i for i in three if i.kind == "strategy").headline


def test_strategy_stands_without_a_pit_loss_figure() -> None:
    """Pit loss is computed on demand, so it is often absent."""
    found = build_insights(_field([0.0, 0.2]), winner_number="1", optimal_stops=2)
    strategy = next(i for i in found if i.kind == "strategy")
    assert "a stop" not in strategy.headline
    assert strategy.magnitude == 2.0


def test_a_driver_without_a_code_falls_back_to_the_car_number() -> None:
    field = _field([0.0, 0.2])
    field[0].abbreviation = None
    found = build_insights(field, winner_number="2")
    assert "car 1" in found[0].headline
