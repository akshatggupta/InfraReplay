"""v4: dynamic values swapped before a request goes back out."""

from infrareplay.replay.substitution import AutoMode, SubstitutionEngine

_UUID = "3fa85f64-5717-4562-b3fc-2c963f66afa6"


def test_literal_mapping_replaces_exact_values():
    engine = SubstitutionEngine({"tok_old": "tok_new"})

    assert engine.apply({"token": "tok_old", "keep": "tok_older"}) == {
        "token": "tok_new",
        "keep": "tok_older",
    }


def test_templates_expand_once_per_run():
    engine = SubstitutionEngine({"cart_1": "${uuid}"})

    first = engine.apply({"cart": "cart_1"})["cart"]
    second = engine.apply({"cart": "cart_1"})["cart"]

    assert first == second
    assert first != "${uuid}"


def test_auto_mode_gives_each_uuid_one_stable_replacement():
    engine = SubstitutionEngine(auto=AutoMode.FRESH_IDS)

    out = engine.apply({"id": _UUID, "ref": {"id": _UUID}, "plain": "not-a-uuid"})

    assert out["id"] != _UUID
    assert out["id"] == out["ref"]["id"]
    assert out["plain"] == "not-a-uuid"


def test_off_by_default():
    assert SubstitutionEngine().apply({"id": _UUID}) == {"id": _UUID}
