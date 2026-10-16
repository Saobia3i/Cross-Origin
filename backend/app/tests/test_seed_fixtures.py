from pathlib import Path

import pytest
from sqlmodel import Session, func, select

from app import db
from app.models import (
    FareMatrixRow, FareRule, Hub, MetroFareMatrix, ModeRate, Report, RouteEdge, ScenarioMultiplier, User,
    WealthIndex,
)
from app.security import verify_password
from app.services import seeder
from app.services.seeder import seed

DEMO_EMAILS = ["admin@dhakapath.local", "user1@dhakapath.local", "user2@dhakapath.local", "user3@dhakapath.local"]


def counts(session):
    models = (User, Hub, RouteEdge, FareMatrixRow, MetroFareMatrix, ModeRate, ScenarioMultiplier,
              WealthIndex, FareRule, Report)
    return {m.__tablename__: session.exec(select(func.count()).select_from(m)).one() for m in models}


@pytest.fixture
def seeded(session):
    seed(session, "fixtures")
    return session


def test_demo_users(seeded):
    users = {u.email: u for u in seeded.exec(select(User)).all()}
    assert sorted(users) == sorted(DEMO_EMAILS)
    admin = users["admin@dhakapath.local"]
    assert admin.role == "admin" and admin.is_verified and admin.status == "active"
    for email in DEMO_EMAILS[1:]:
        assert users[email].role == "user" and users[email].is_verified
    assert all(verify_password("password123", u.password_hash) for u in users.values())
    assert "password123" not in admin.password_hash


def test_fixture_seed_creates_hubs_and_routes(seeded):
    hubs = {h.hub_id: h for h in seeded.exec(select(Hub)).all()}
    assert sorted(hubs) == ["FARMGATE", "MIRPUR_10", "UTTARA_NORTH"]
    assert hubs["MIRPUR_10"].wealth_index == 1.23 and hubs["MIRPUR_10"].aliases_json == ["Mirpur"]
    assert hubs["UTTARA_NORTH"].type == "metro_station"
    assert hubs["FARMGATE"].type == "metro_station"
    edges = {e.route_id: e for e in seeded.exec(select(RouteEdge)).all()}
    assert sorted(edges) == ["LOCAL_BUS_FARMGATE_UTTARA_NORTH", "MRT6_MIRPUR_10_FARMGATE",
                             "MRT6_UTTARA_NORTH_MIRPUR_10"]
    bus = edges["LOCAL_BUS_FARMGATE_UTTARA_NORTH"]
    assert (bus.mode, bus.status, bus.direction, bus.student_eligible) == ("local_bus", "closed", "one_way", True)
    assert bus.closed_reason == "dataset status: closed" and bus.road_distance_km == 9.99
    mrt = edges["MRT6_UTTARA_NORTH_MIRPUR_10"]
    assert mrt.mode == "mrt" and mrt.direction == "unknown" and mrt.geometry_json["type"] == "LineString"
    assert not mrt.student_eligible


def test_fixture_seed_creates_rate_and_fare_tables(seeded):
    c = counts(seeded)
    assert (c["fare_matrix_rows"], c["metro_fare_matrix"], c["mode_rates"], c["wealth_indices"]) == (1, 6, 2, 1)
    assert c["scenario_multipliers"] == 14 and c["fare_rules"] == 12
    row = seeded.exec(select(FareMatrixRow)).one()
    assert (row.mode, row.from_hub, row.to_hub) == ("local_bus", "FARMGATE", "UTTARA_NORTH")
    assert (row.normal_low_bdt, row.normal_high_bdt) == (11, 12)
    assert (row.student_fare_low_bdt, row.student_fare_high_bdt) == (10, 10)
    rick = seeded.get(ModeRate, "rickshaw")
    assert (rick.base_fare_bdt, rick.per_km_bdt, rick.min_fare_bdt) == (11, 22, 33)
    peak_plus_rain = seeded.exec(select(ScenarioMultiplier).where(
        ScenarioMultiplier.mode == "rickshaw", ScenarioMultiplier.scenario == "peak_plus_rain")).one()
    assert peak_plus_rain.multiplier is None
    metro = seeded.exec(select(MetroFareMatrix).where(
        MetroFareMatrix.from_station_hub_id == "UTTARA_NORTH", MetroFareMatrix.to_station_hub_id == "FARMGATE")).one()
    assert metro.fare_bdt == 22


def test_fare_rules_defaults(seeded):
    rules = {r.mode: r for r in seeded.exec(select(FareRule)).all()}
    assert (rules["local_bus"].student_discount_percent, rules["local_bus"].minimum_student_fare_bdt) == (50, 10)
    for mode, rule in rules.items():
        if mode != "local_bus":
            assert rule.student_discount_percent == 0, mode
    assert rules["mrt"].use_official_matrix and not rules["mrt"].allow_formula_fallback
    assert not rules["walk"].allow_formula_fallback
    assert rules["rickshaw"].allow_formula_fallback and not rules["rickshaw"].use_official_matrix


def test_fixture_reports_only_in_fixture_mode(seeded):
    reports = {r.report_id: r for r in seeded.exec(select(Report)).all()}
    assert sorted(reports) == ["rpt_TEST_FIXTURE_001", "rpt_TEST_FIXTURE_002"]
    assert reports["rpt_TEST_FIXTURE_001"].affected_hub == "MIRPUR_10"
    assert reports["rpt_TEST_FIXTURE_002"].status == "verified" and reports["rpt_TEST_FIXTURE_002"].admin_approved


def test_seed_is_idempotent(session):
    first = seed(session, "fixtures")
    before = counts(session)
    second = seed(session, "fixtures")
    assert counts(session) == before
    assert all(v == 0 for c in second.counts.values() for v in c.values())
    assert first.counts["hubs"]["inserted"] == 3


def test_reseed_preserves_runtime_state(session):
    seed(session, "fixtures")
    edge = session.get(RouteEdge, "MRT6_UTTARA_NORTH_MIRPUR_10")
    edge.status, edge.closed_reason, edge.flood_prone = "closed", "admin closed", True
    rule = session.get(FareRule, "rickshaw")
    rule.student_discount_percent, rule.surge_allowed = 25, True
    report = session.get(Report, "rpt_TEST_FIXTURE_001")
    report.upvotes = 7
    session.commit()
    seed(session, "fixtures")
    session.expire_all()
    edge = session.get(RouteEdge, "MRT6_UTTARA_NORTH_MIRPUR_10")
    assert (edge.status, edge.closed_reason, edge.flood_prone) == ("closed", "admin closed", True)
    assert session.get(FareRule, "rickshaw").student_discount_percent == 25
    assert session.get(Report, "rpt_TEST_FIXTURE_001").upvotes == 7


def test_users_not_rehashed_or_duplicated(session):
    seed(session, "fixtures")
    h = session.exec(select(User).where(User.email == DEMO_EMAILS[0])).one().password_hash
    seed(session, "fixtures")
    assert session.exec(select(func.count()).select_from(User)).one() == 4
    assert session.exec(select(User).where(User.email == DEMO_EMAILS[0])).one().password_hash == h


# ---- normalized source ------------------------------------------------------------
def test_normalized_seed_with_missing_files_seeds_users_only(session, tmp_path):
    result = seed(session, "normalized", tmp_path)  # empty directory
    c = counts(session)
    assert c["users"] == 4 and c["fare_rules"] == 12
    assert all(c[t] == 0 for t in ("hubs", "route_edges", "fare_matrix_rows", "metro_fare_matrix",
                                   "mode_rates", "scenario_multipliers", "wealth_indices", "reports"))
    assert any("normalized files missing" in w for w in result.warnings)
    assert any("no transit data seeded" in w for w in result.warnings)
    rule = session.get(FareRule, "local_bus")
    assert (rule.student_discount_percent, rule.minimum_student_fare_bdt) == (50, 10)  # section-rules default


def test_normalized_seed_with_partial_and_invalid_files(session, tmp_path):
    (tmp_path / "normalized_hubs.json").write_text("{not json", encoding="utf-8")
    result = seed(session, "normalized", tmp_path)
    assert counts(session)["hubs"] == 0
    assert any("not valid JSON" in w for w in result.warnings)


def test_normalized_seed_refuses_fixture_records(session, tmp_path):
    (tmp_path / "normalized_hubs.json").write_text(
        '[{"hub_id": "X", "name": "X", "is_test_fixture": true}]', encoding="utf-8")
    with pytest.raises(ValueError, match="test-fixture"):
        seed(session, "normalized", tmp_path)


def test_normalized_seed_from_generated_files(session, tmp_path):
    """Round trip: normalize the fixtures to disk, then seed them back as 'normalized'."""
    from app.services.dataset_loader import build_manifest
    from app.services.dataset_normalizer import normalize

    out = tmp_path / "gen"
    report = normalize(build_manifest("fixtures"), out)
    # strip the fixture flag the way a production run would never have it
    import json
    for name in ("normalized_hubs.json", "normalized_route_edges.json"):
        records = json.loads((out / name).read_text(encoding="utf-8"))
        for r in records:
            r["is_test_fixture"] = False
        (out / name).write_text(json.dumps(records), encoding="utf-8")
    result = seed(session, "normalized", out)
    c = counts(session)
    assert c["hubs"] == 3 and c["route_edges"] == 3 and c["fare_matrix_rows"] == 1
    assert c["reports"] == 0  # no reports file => no fabricated alerts
    assert report["outputs"]["normalized_hubs.json"] == 3 and not result.warnings


def test_switching_sources_requires_reset(session, engine):
    seed(session, "fixtures")
    with pytest.raises(RuntimeError, match="--reset"):
        seed(session, "normalized", Path("."))
    session.rollback()
    db.reset_db(engine)
    with Session(engine) as fresh:
        seed(fresh, "normalized", Path(__file__).parent)  # no normalized files here
        assert counts(fresh)["hubs"] == 0


def test_stale_rows_are_removed_on_reseed(session):
    seed(session, "fixtures")
    session.add(Hub(hub_id="STALE", name="stale", raw_json={"is_test_fixture": True}))
    session.add(WealthIndex(hub_name="Ghost", wealth_index=1.0))
    session.commit()
    result = seed(session, "fixtures")
    assert session.get(Hub, "STALE") is None and session.get(WealthIndex, "Ghost") is None
    assert result.counts["hubs"]["removed"] == 1


def test_unknown_source_rejected(session):
    with pytest.raises(ValueError):
        seed(session, "production")


def test_real_database_untouched_by_tests(engine):
    assert "dhakapath.db" not in str(engine.url)
