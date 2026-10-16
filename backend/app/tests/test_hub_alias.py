import pytest

from app.services.hub_alias import HubRegistry, compact_key, make_hub_id

CONFIG = {
    "required_aliases": [
        {"alias": "Uttara", "canonical": "Uttara North", "source": "problem_owner_brief"},
        {"alias": "Mirpur", "canonical": "Mirpur 10", "source": "problem_owner_brief"},
    ],
    "approved_aliases": [],
    "rejected_aliases": [],
    "preferred_display_names": ["Farmgate", "Mirpur 10", "Uttara North"],
}


@pytest.mark.parametrize("name,expected", [
    ("Mirpur 10", "MIRPUR_10"),
    ("Uttara North", "UTTARA_NORTH"),
    ("Farmgate", "FARMGATE"),
    ("Motijheel", "MOTIJHEEL"),
    ("Shahbagh", "SHAHBAGH"),
    ("Dhaka Univ.", "DHAKA_UNIV"),
    ("Mirpur10", "MIRPUR_10"),
    ("  Ansar   camp ", "ANSAR_CAMP"),
    ("Car (Taxi / hired car)", "CAR_TAXI_HIRED_CAR"),
])
def test_make_hub_id(name, expected):
    assert make_hub_id(name) == expected


def test_non_ascii_name_gets_stable_fallback_id():
    assert make_hub_id("মতিঝিল") == make_hub_id("মতিঝিল")
    assert make_hub_id("মতিঝিল").startswith("HUB_")


def test_required_aliases():
    reg = HubRegistry(CONFIG)
    for n in ("Uttara", "Uttara North", "Mirpur", "Mirpur 10", "Mirpur10"):
        reg.observe(n)
    assert reg.hub_id("Uttara") == "UTTARA_NORTH"
    assert reg.hub_id("Mirpur") == "MIRPUR_10"
    assert reg.hub_id("Mirpur10") == "MIRPUR_10"
    assert reg.display_name("Mirpur") == "Mirpur 10"


def test_alias_applies_even_if_only_alias_is_observed():
    reg = HubRegistry(CONFIG)
    reg.observe("Uttara")
    assert reg.hub_id("Uttara") == "UTTARA_NORTH"
    assert reg.aliases(reg.group_key("Uttara")) == ["Uttara"]


def test_formatting_variants_merge_but_spelling_variants_do_not():
    reg = HubRegistry(CONFIG)
    for n in ("Farm Gate", "Farmgate", "Farm gate", "Firmgate", "Shahbag", "Shahbagh", "Sisu Mela", "Sisumela"):
        reg.observe(n)
    assert reg.hub_id("Farm Gate") == reg.hub_id("Farm gate") == "FARMGATE"  # preferred display wins
    assert reg.hub_id("Sisumela") == "SISU_MELA"  # more word separators wins
    assert reg.hub_id("Firmgate") == "FIRMGATE"
    assert reg.hub_id("Shahbag") != reg.hub_id("Shahbagh")
    proposed = {(p["hub_id_a"], p["hub_id_b"]) for p in reg.proposals()}
    assert ("SHAHBAG", "SHAHBAGH") in proposed
    assert ("FARMGATE", "FIRMGATE") in proposed
    assert all(p["applied"] is False for p in reg.proposals())


def test_different_numbers_never_merge_or_get_proposed():
    reg = HubRegistry(CONFIG)
    for n in ("Mirpur 10", "Mirpur 11", "Mirpur 1", "Mirpur 12"):
        reg.observe(n)
    ids = {reg.hub_id(n) for n in ("Mirpur 10", "Mirpur 11", "Mirpur 1", "Mirpur 12")}
    assert len(ids) == 4
    assert reg.proposals() == []


def test_approved_and_rejected_aliases():
    cfg = {**CONFIG,
           "approved_aliases": [{"alias": "Shahbag", "canonical": "Shahbagh", "source": "review"}],
           "rejected_aliases": [{"alias": "Firmgate", "canonical": "Farmgate"}]}
    reg = HubRegistry(cfg)
    for n in ("Shahbag", "Shahbagh", "Firmgate", "Farmgate"):
        reg.observe(n)
    assert reg.hub_id("Shahbag") == "SHAHBAGH"
    assert reg.proposals() == []


def test_dataset_declared_alias_confirms_required_alias():
    reg = HubRegistry(CONFIG)
    reg.add_alias("Mirpur", "Mirpur 10", "rules.csv (Disclaimer)")
    reg.observe("Mirpur")
    entry = next(e for e in reg.applied_alias_entries() if e["alias"] == "Mirpur")
    assert entry["confirmed_by"] == ["rules.csv (Disclaimer)"]


def test_hub_id_does_not_depend_on_observation_order():
    a, b = HubRegistry(CONFIG), HubRegistry(CONFIG)
    names = ["MES Busstop", "MES BUS Stop", "Asadgate", "Asad Gate"]
    for n in names:
        a.observe(n)
    for n in reversed(names):
        b.observe(n)
    assert [a.hub_id(n) for n in names] == [b.hub_id(n) for n in names]


def test_compact_key():
    assert compact_key("Farm-Gate") == compact_key("farmgate") == "FARMGATE"
