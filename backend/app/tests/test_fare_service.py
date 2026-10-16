"""Fare engine tests on a synthetic world. Rates/multipliers below are the problem owner's published
Section-rules numbers; hubs, distances and explicit fare rows are made up for the tests only."""
import math

import pytest
from sqlmodel import select

from app.models import (
    FareMatrixRow, FareRule, Hub, MetroFareMatrix, ModeRate, RouteEdge, ScenarioMultiplier,
)
from app.schemas import LegFareResult
from app.services import fare_service as fs
from app.services.fare_service import FareError, make_range
from app.services.seeder import SeedData, SeedResult, seed_fare_rules

RATES = {  # mode: (base, per_km, min, low_band, high_band)
    "rickshaw": (15, 25, 30, 0.85, 1.25),
    "cng": (30, 38, 70, 0.9, 1.25),
    "local_bus": (0, 2.45, 10, 1, 1),
}
MULTS = {  # mode: rain, peak, detour, regular_holiday, festive_holiday
    "rickshaw": (1.6, 1.25, 1.5, 0.95, 1.5),
    "cng": (1.5, 1.3, 1.4, 0.9, 1.4),
    "local_bus": (1.0, 1.0, 1.15, 0.95, 1.1),
}


@pytest.fixture
def db(session):
    for hub_id, lat, lon in (("A", 23.70, 90.40), ("B", 23.75, 90.40), ("C", None, None)):
        session.add(Hub(hub_id=hub_id, name=hub_id, latitude=lat, longitude=lon))
    for mode, (b, k, m, lo, hi) in RATES.items():
        session.add(ModeRate(mode=mode, base_fare_bdt=b, per_km_bdt=k, min_fare_bdt=m,
                             low_band_multiplier=lo, high_band_multiplier=hi))
        rain, peak, detour, reg, fest = MULTS[mode]
        for scen, v in (("normal", 1.0), ("rain", rain), ("peak", peak), ("peak_plus_rain", None),
                        ("detour", detour), ("regular_holiday", reg), ("festive_holiday", fest)):
            session.add(ScenarioMultiplier(mode=mode, scenario=scen, multiplier=v))
    session.add(MetroFareMatrix(from_station_hub_id="A", to_station_hub_id="B", fare_bdt=40))
    session.add(MetroFareMatrix(from_station_hub_id="B", to_station_hub_id="A", fare_bdt=40))
    session.commit()
    seed_fare_rules(session, SeedData("fixtures"), SeedResult("fixtures"))  # local_bus 50% / min 10
    session.commit()
    return session


def edge(session, mode, route_id=None, o="A", d="B", km=None, **kw):
    e = RouteEdge(route_id=route_id or f"{mode}_{o}_{d}", mode=mode, origin_hub=o, destination_hub=d,
                  road_distance_km=km, **kw)
    session.add(e)
    session.commit()
    return e


def row(session, mode, o="A", d="B", **cols):
    r = FareMatrixRow(mode=mode, from_hub=o, to_hub=d, **cols)
    session.add(r)
    session.commit()
    return r


def regular(leg):
    return leg.regular_fare_min_bdt, leg.regular_fare_max_bdt


# ---- metro ----------------------------------------------------------------------
def test_metro_exact_and_unaffected_by_rain(db):
    e = edge(db, "mrt", km=3)
    row(db, "mrt", normal_low_bdt=999, normal_high_bdt=999, rain_low_bdt=999, rain_high_bdt=999,
        area_wealth_multiplier=1.5)  # dataset metro row must not override the official matrix
    for scenario in ("normal", "rain", "peak", "detour", "festive_holiday"):
        leg = fs.calculate_leg_fare(db, e, scenario)
        assert regular(leg) == (40, 40) and leg.fare_source == "metro_matrix"
        assert leg.student_discount_applies is False
    assert fs.calculate_metro_fare(db, "B", "A").is_range is False  # reverse direction works


def test_metro_no_student_discount_by_default(db):
    leg = fs.calculate_leg_fare(db, edge(db, "mrt"), "normal", prefer_student=True)
    assert (leg.student_fare_min_bdt, leg.student_fare_max_bdt) == (40, 40)
    assert leg.student_discount_applies is False


def test_metro_unknown_pair_fails_clearly(db):
    with pytest.raises(FareError) as exc:
        fs.calculate_leg_fare(db, edge(db, "mrt", d="C"), "normal")
    assert exc.value.code == "METRO_FARE_UNKNOWN"


# ---- student fare ---------------------------------------------------------------
@pytest.mark.parametrize("regular_fare,expected", [(30, 15), (15, 10), (10, 10), (21, 11), (100, 50)])
def test_local_bus_student_fare(db, regular_fare, expected):
    student, applies = fs.calculate_student_fare(
        db, "local_bus", make_range(regular_fare, regular_fare, "formula"), "normal", None)
    assert applies is True
    assert (student.min_bdt, student.max_bdt) == (expected, expected)


def test_local_bus_student_via_leg(db):
    e = edge(db, "local_bus", km=5)
    row(db, "local_bus", normal_low_bdt=30, normal_high_bdt=30)  # no explicit student columns
    leg = fs.calculate_leg_fare(db, e, "normal", prefer_student=True)
    assert regular(leg) == (30, 30)
    assert (leg.student_fare_min_bdt, leg.student_fare_max_bdt) == (15, 15)
    assert leg.student_discount_applies is True


def test_explicit_student_columns_win_in_normal_scenario(db):
    e = edge(db, "local_bus", km=5)
    row(db, "local_bus", normal_low_bdt=21, normal_high_bdt=21, student_fare_low_bdt=10, student_fare_high_bdt=10,
        rain_low_bdt=30, rain_high_bdt=30)
    assert fs.calculate_leg_fare(db, e, "normal", True).student_fare_min_bdt == 10
    rainy = fs.calculate_leg_fare(db, e, "rain", True)  # explicit student is a normal-scenario value
    assert (rainy.student_fare_min_bdt, rainy.student_fare_max_bdt) == (15, 15)


def test_student_discount_does_not_apply_to_rickshaw(db):
    e = edge(db, "rickshaw", km=5)
    row(db, "rickshaw", normal_low_bdt=120, normal_high_bdt=180, rain_low_bdt=195, rain_high_bdt=285,
        student_fare_low_bdt=120, student_fare_high_bdt=180)
    for scenario, expected in (("normal", (120, 180)), ("rain", (195, 285))):
        leg = fs.calculate_leg_fare(db, e, scenario, prefer_student=True)
        assert (leg.student_fare_min_bdt, leg.student_fare_max_bdt) == expected == regular(leg)
        assert leg.student_discount_applies is False


def test_student_fields_mirror_regular_when_not_requested(db):
    e = edge(db, "local_bus", km=5)
    row(db, "local_bus", normal_low_bdt=30, normal_high_bdt=30)
    leg = fs.calculate_leg_fare(db, e, "normal", prefer_student=False)
    assert (leg.student_fare_min_bdt, leg.student_discount_applies) == (30, False)


def test_student_rule_is_admin_editable(db):
    db.get(FareRule, "local_bus").student_discount_percent = 25
    db.get(FareRule, "local_bus").minimum_student_fare_bdt = 5
    db.commit()
    student, _ = fs.calculate_student_fare(db, "local_bus", make_range(40, 40, "formula"), "normal", None)
    assert student.min_bdt == 30


# ---- explicit rows & ranges ------------------------------------------------------
def test_rickshaw_returns_a_range(db):
    e = edge(db, "rickshaw", km=5)
    row(db, "rickshaw", normal_low_bdt=120, normal_high_bdt=180)
    leg = fs.calculate_leg_fare(db, e, "normal")
    assert regular(leg) == (120, 180) and leg.regular_fare_min_bdt < leg.regular_fare_max_bdt
    assert leg.fare_source == "explicit_fare_row"
    assert fs.calculate_explicit_fare(db.exec(select(FareMatrixRow)).first(), "normal").is_range is True


def test_peak_plus_rain_uses_explicit_column(db):
    e = edge(db, "rickshaw", km=5)
    row(db, "rickshaw", normal_low_bdt=120, normal_high_bdt=180, peak_plus_rain_low_bdt=245, peak_plus_rain_high_bdt=360)
    leg = fs.calculate_leg_fare(db, e, "peak_plus_rain")
    assert regular(leg) == (245, 360) and leg.fare_source == "explicit_fare_row" and not leg.warnings


def test_detour_uses_explicit_blockage_column(db):
    e = edge(db, "cng", km=5)
    row(db, "cng", normal_low_bdt=205, normal_high_bdt=280,
        blockage_construction_waterlog_detour_low_bdt=285, blockage_construction_waterlog_detour_high_bdt=395)
    assert regular(fs.calculate_leg_fare(db, e, "detour")) == (285, 395)


def test_explicit_scenario_columns_map(db):
    r = row(db, "rickshaw", normal_low_bdt=1, normal_high_bdt=2, rain_low_bdt=3, rain_high_bdt=4,
            peak_office_hour_low_bdt=5, peak_office_hour_high_bdt=6, regular_holiday_low_bdt=7,
            regular_holiday_high_bdt=8, festive_holiday_low_bdt=9, festive_holiday_high_bdt=10)
    got = {s: (lambda f: (f.min_bdt, f.max_bdt))(fs.calculate_explicit_fare(r, s))
           for s in ("normal", "rain", "peak", "regular_holiday", "festive_holiday")}
    assert got == {"normal": (1, 2), "rain": (3, 4), "peak": (5, 6), "regular_holiday": (7, 8), "festive_holiday": (9, 10)}
    assert fs.calculate_explicit_fare(r, "detour") is None and fs.calculate_explicit_fare(r, "student") is None


def test_explicit_row_matches_reverse_direction(db):
    row(db, "rickshaw", o="B", d="A", normal_low_bdt=120, normal_high_bdt=180)
    assert regular(fs.calculate_leg_fare(db, edge(db, "rickshaw", o="A", d="B", km=5), "normal")) == (120, 180)


# ---- formula ----------------------------------------------------------------------
def test_formula_normal_rickshaw_is_a_range(db):
    leg = fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "normal")
    assert leg.fare_source == "formula"
    assert regular(leg) == (pytest.approx(119.0), pytest.approx(175.0))  # 140 x 0.85 / x 1.25
    assert leg.regular_fare_min_bdt < leg.regular_fare_max_bdt and leg.distance_km == 5


def test_rickshaw_rain_multiplier_applied(db):
    lo, hi = regular(fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "rain"))
    assert (lo, hi) == (pytest.approx(119 * 1.6), pytest.approx(175 * 1.6))


def test_cng_peak_multiplier_applied(db):
    lo, hi = regular(fs.calculate_leg_fare(db, edge(db, "cng", km=5), "peak"))
    assert (lo, hi) == (pytest.approx(220 * 0.9 * 1.3), pytest.approx(220 * 1.25 * 1.3))  # 30 + 38x5 = 220


def test_formula_minimum_fare_applies(db):
    lo, hi = regular(fs.calculate_leg_fare(db, edge(db, "rickshaw", km=0.1), "normal"))
    # base 15 + 2.5 = 17.5 < min 30 -> normal 30; band gives 25.5 / 37.5; final min lifts the low end
    assert (lo, hi) == (30, pytest.approx(37.5))


def test_peak_plus_rain_formula_is_derived_with_warning(db):
    leg = fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "peak_plus_rain")
    assert regular(leg)[0] == pytest.approx(119 * 1.6 * 1.25)
    assert fs.get_scenario_multiplier(db, "rickshaw", "peak_plus_rain") == pytest.approx(2.0)
    assert fs._scenario_multiplier(db, "rickshaw", "peak_plus_rain")[1]  # carries an explanatory note


def test_scenario_alias_and_unknown_scenario(db):
    assert fs.normalize_scenario("heavy_rain") == "rain"
    with pytest.raises(FareError) as exc:
        fs.normalize_scenario("snow")
    assert exc.value.code == "SCENARIO_UNKNOWN"


# ---- wealth multiplier -------------------------------------------------------------
def test_wealth_multiplier_applied_to_non_metro_only():
    base = make_range(100, 200, "formula")
    out = fs.apply_wealth_multiplier(base, 1.1, "rickshaw")
    assert (out.min_bdt, out.max_bdt) == (pytest.approx(110), pytest.approx(220))
    for same in (fs.apply_wealth_multiplier(base, 1.1, "mrt"), fs.apply_wealth_multiplier(base, None, "rickshaw"),
                 fs.apply_wealth_multiplier(base, "1.1", "rickshaw"), fs.apply_wealth_multiplier(base, True, "cng")):
        assert (same.min_bdt, same.max_bdt) == (100, 200)


def test_formula_uses_the_rows_explicit_wealth_multiplier(db):
    row(db, "rickshaw", normal_low_bdt=120, normal_high_bdt=180, area_wealth_multiplier=1.025)  # no rain columns
    leg = fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "rain")
    assert leg.fare_source == "formula"
    assert regular(leg)[0] == pytest.approx(119 * 1.6 * 1.025)
    assert any("no rain value" in w for w in leg.warnings)


def test_no_wealth_adjustment_without_explicit_multiplier(db):
    leg = fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "normal")
    assert regular(leg)[0] == pytest.approx(119)


# ---- distance ------------------------------------------------------------------------
def test_distance_from_straight_line(db):
    e = edge(db, "rickshaw", straight_line_km=4)
    assert fs.calculate_distance_km(e, None, None) == pytest.approx(5.4)


def test_distance_from_hub_coordinates_uses_haversine_times_1_35(db):
    e = edge(db, "rickshaw")
    km = fs.calculate_distance_km(e, db.get(Hub, "A"), db.get(Hub, "B"))
    assert km == pytest.approx(0.05 * 111.19 * 1.35, rel=0.01)  # 0.05 deg latitude
    assert fs.calculate_leg_fare(db, e, "normal").distance_km == pytest.approx(km)


def test_distance_unknown_fails_instead_of_guessing(db):
    e = edge(db, "rickshaw", d="C")  # C has no coordinates, edge has no distance
    with pytest.raises(FareError) as exc:
        fs.calculate_leg_fare(db, e, "normal")
    assert exc.value.code == "DISTANCE_UNKNOWN"


def test_missing_mode_rate_fails_clearly(db):
    with pytest.raises(FareError) as exc:
        fs.calculate_leg_fare(db, edge(db, "car", km=5), "normal")
    assert exc.value.code == "MODE_RATE_MISSING"


# ---- walk ------------------------------------------------------------------------------
def test_walk_fare_is_zero(db):
    leg = fs.calculate_leg_fare(db, edge(db, "walk"), "rain", prefer_student=True)
    assert regular(leg) == (0, 0) and (leg.student_fare_min_bdt, leg.student_fare_max_bdt) == (0, 0)
    assert leg.fare_source == "walk" and leg.student_discount_applies is False


# ---- trip ----------------------------------------------------------------------------------
def test_trip_fare_sums_min_and_max(db):
    e1, e2, e3 = edge(db, "rickshaw", "r1", km=5), edge(db, "local_bus", "b1", km=5), edge(db, "mrt", "m1")
    row(db, "rickshaw", normal_low_bdt=120, normal_high_bdt=180)
    row(db, "local_bus", normal_low_bdt=30, normal_high_bdt=40)
    legs = [fs.calculate_leg_fare(db, e, "normal", prefer_student=True) for e in (e1, e2, e3)]
    trip = fs.calculate_trip_fare(legs, prefer_student=True)
    assert (trip.total_regular_fare_min_bdt, trip.total_regular_fare_max_bdt) == (120 + 30 + 40, 180 + 40 + 40)
    assert (trip.total_student_fare_min_bdt, trip.total_student_fare_max_bdt) == (120 + 15 + 40, 180 + 20 + 40)
    assert trip.student_discount_applies_any_leg is True and trip.currency == "BDT"
    plain = fs.calculate_trip_fare(legs, prefer_student=False)
    assert plain.total_student_fare_min_bdt == plain.total_regular_fare_min_bdt
    assert plain.student_discount_applies_any_leg is False
    assert any("estimates" in w for w in trip.warnings)


def test_empty_trip_is_zero():
    trip = fs.calculate_trip_fare([])
    assert (trip.total_regular_fare_min_bdt, trip.total_regular_fare_max_bdt) == (0, 0)


# ---- ride-share ------------------------------------------------------------------------------
def test_rideshare_split_two_participants():
    r = fs.calculate_rideshare_split(make_range(200, 300, "explicit_fare_row"), 2)
    assert (r.per_person_min_bdt, r.per_person_max_bdt) == (100, 150)
    assert (r.total_fare_min_bdt, r.total_fare_max_bdt, r.participants) == (200, 300, 2)
    assert r.per_person_min_bdt < r.per_person_max_bdt  # still a range


def test_suggested_safe_amount_is_ceil_of_per_person_max():
    r = fs.calculate_rideshare_split(make_range(250, 250.5, "formula"), 3)
    assert r.per_person_max_bdt == pytest.approx(83.5)
    assert r.suggested_safe_per_person_bdt == math.ceil(250.5 / 3) == 84
    assert isinstance(r.suggested_safe_per_person_bdt, int)
    exact = fs.calculate_rideshare_split(make_range(60, 90, "formula"), 3)
    assert exact.suggested_safe_per_person_bdt == 30  # no float-noise bump to 31
    assert "upper-bound" in r.note and "estimate range" in r.note


def test_rideshare_rounds_only_when_serialized():
    r = fs.calculate_rideshare_split(make_range(100, 200, "formula"), 3)
    assert r.per_person_min_bdt == pytest.approx(100 / 3) and r.per_person_min_bdt != round(100 / 3, 2)
    dumped = r.model_dump()
    assert dumped["per_person_min_bdt"] == 33.33 and dumped["per_person_max_bdt"] == 66.67
    assert "33.33" in r.model_dump_json()


@pytest.mark.parametrize("n", [0, -1, 2.5, True])
def test_rideshare_rejects_bad_participants(n):
    with pytest.raises(FareError) as exc:
        fs.calculate_rideshare_split(make_range(10, 20, "formula"), n)
    assert exc.value.code == "INVALID_PARTICIPANTS"


# ---- sanity validation ---------------------------------------------------------------------------
def test_unrealistic_rickshaw_fare_is_flagged(db):
    bad = fs.validate_submitted_fare(db, "rickshaw", 5000, 5.0, "normal")
    assert bad.checked and not bad.is_realistic and bad.needs_admin_review and bad.reasons
    ok = fs.validate_submitted_fare(db, "rickshaw", 150, 5.0, "normal")
    assert ok.checked and ok.is_realistic and not ok.needs_admin_review


def test_sanity_threshold_is_five_times_expected_max(db):
    high = fs.calculate_normal_formula_fare(db, "cng", 5).max_bdt  # 275
    assert fs.validate_submitted_fare(db, "cng", high * 5, 5.0).is_realistic
    assert not fs.validate_submitted_fare(db, "cng", high * 5 + 1, 5.0).is_realistic


def test_sanity_local_bus_and_negative(db):
    assert not fs.validate_submitted_fare(db, "local_bus", -1, 5.0).is_realistic
    assert not fs.validate_submitted_fare(db, "local_bus", 1000, 5.0).is_realistic  # expected max 12.25
    assert fs.validate_submitted_fare(db, "local_bus", 30, 5.0).is_realistic


def test_sanity_uses_scenario(db):
    normal_high = fs.calculate_normal_formula_fare(db, "rickshaw", 5).max_bdt
    r = fs.validate_submitted_fare(db, "rickshaw", normal_high * 7, 5.0, "rain")  # 175 x 7 < 5 x 280? no: 1225 < 1400
    assert r.is_realistic and r.expected.max_bdt == pytest.approx(normal_high * 1.6)


def test_sanity_metro_compares_with_official_matrix(db):
    assert fs.validate_submitted_fare(db, "mrt", 40, None, "normal", "A", "B").is_realistic
    wrong = fs.validate_submitted_fare(db, "mrt", 60, None, "normal", "A", "B")
    assert not wrong.is_realistic and wrong.needs_admin_review and wrong.expected.min_bdt == 40


def test_sanity_cannot_check_without_data_does_not_flag(db):
    unknown_distance = fs.validate_submitted_fare(db, "rickshaw", 5000, None)
    assert not unknown_distance.checked and unknown_distance.is_realistic and not unknown_distance.needs_admin_review
    assert unknown_distance.reasons
    no_stations = fs.validate_submitted_fare(db, "mrt", 999, None)
    assert not no_stations.checked and not no_stations.needs_admin_review


# ---- schema / serialization --------------------------------------------------------------------
def test_fare_range_schema_shape():
    r = make_range(10, 10, "metro_matrix", ["n"])
    assert r.model_dump() == {"min_bdt": 10, "max_bdt": 10, "currency": "BDT", "is_range": False,
                              "source": "metro_matrix", "notes": ["n"]}
    assert make_range(5, 3, "formula").min_bdt == 3  # always ordered


def test_leg_result_schema_fields(db):
    leg = fs.calculate_leg_fare(db, edge(db, "walk"), "normal")
    assert set(leg.model_dump()) == {
        "route_id", "mode", "regular_fare_min_bdt", "regular_fare_max_bdt", "student_fare_min_bdt",
        "student_fare_max_bdt", "student_discount_applies", "scenario", "distance_km", "fare_source", "warnings"}
    assert isinstance(leg, LegFareResult)


def test_legs_serialize_with_two_decimals(db):
    leg = fs.calculate_leg_fare(db, edge(db, "rickshaw", km=5), "rain")
    assert leg.regular_fare_min_bdt == pytest.approx(190.4) and leg.model_dump()["regular_fare_max_bdt"] == 280.0
    assert fs.calculate_leg_fare(db, edge(db, "cng", km=5), "peak").model_dump()["regular_fare_min_bdt"] == 257.4
