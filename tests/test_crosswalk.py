from recon.crosswalk import AliasStore, resolve
from recon.models import ContractItem, UoM


def _contract():
    return [
        ContractItem("3.1", "Place 144ct ADSS aerial fiber", UoM.FT, 1.85, 42000),
        ContractItem("4.1", "Directional bore 2\"", UoM.FT, 9.50, 6000),
        ContractItem("5.1", "Handhole 30x48", UoM.EA, 340.0, 55),
    ]


def test_alias_hit_is_authoritative():
    store = AliasStore()
    store.confirm("Some Weird Contractor Wording", "5.1")
    m = resolve("some weird contractor wording", _contract(), store)
    assert m.code == "5.1"
    assert m.kind == "alias"
    assert m.score == 100


def test_fuzzy_auto_high_confidence():
    m = resolve("144F ADSS Aerial Place", _contract(), AliasStore())
    assert m.code == "3.1"
    assert m.kind == "auto"
    assert m.score >= 90


def test_below_threshold_goes_to_review():
    m = resolve("Traffic control / flagging (day)", _contract(), AliasStore())
    assert m.code is None
    assert m.kind == "review"
    # still offers candidates for the UI to render
    assert len(m.candidates) >= 1


def test_confirm_writes_normalized_alias():
    store = AliasStore()
    key = store.confirm("Directional Drilling 2 inch", "4.1")
    assert store.get(key) == "4.1"
    # a later lookup of a differently-cased variant resolves via alias
    m = resolve("directional drilling 2 inch", _contract(), store)
    assert m.code == "4.1" and m.kind == "alias"
