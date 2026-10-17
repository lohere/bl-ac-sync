from acsync import MaterialMap


def test_mapping_and_default_naming():
    m = MaterialMap({"Brick - Aged Light": "_Brick"}, prefix="AC ")
    assert m.target_for("Brick - Aged Light") == "_Brick"
    assert m.target_for("Glass - Clear Fast") == "AC Glass - Clear Fast"
    assert m.is_mapped("Brick - Aged Light") and not m.is_mapped("Glass - Clear Fast")


def test_empty_mapping_entry_counts_as_unmapped():
    m = MaterialMap({"Brick - Aged Light": ""})
    assert not m.is_mapped("Brick - Aged Light")
    assert m.target_for("Brick - Aged Light") == "Brick - Aged Light"
