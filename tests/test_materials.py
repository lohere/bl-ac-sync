from acsync import MaterialMap, strip_blender_suffix


def test_strip_blender_suffix():
    assert strip_blender_suffix("Brick - Aged Light.001") == "Brick - Aged Light"
    assert strip_blender_suffix("Brick - Aged Light") == "Brick - Aged Light"
    assert strip_blender_suffix("Door.v2") == "Door.v2"  # not Blender's 3-digit suffix


def test_mapping_and_default_naming():
    m = MaterialMap({"Brick - Aged Light": "_Brick"}, prefix="AC ")
    assert m.target_for("Brick - Aged Light") == "_Brick"
    assert m.target_for("Glass - Clear Fast") == "AC Glass - Clear Fast"
    assert m.is_mapped("Brick - Aged Light") and not m.is_mapped("Glass - Clear Fast")


def test_text_roundtrip_with_comments_and_blank_lines():
    text = "# my mapping\nBrick - Aged Light = _Brick\n\nGlass - Clear Fast=_Glass  # window\nbad line\n"
    m = MaterialMap.from_text(text)
    assert m.mapping == {"Brick - Aged Light": "_Brick", "Glass - Clear Fast": "_Glass"}
    assert MaterialMap.from_text(m.to_text()).mapping == m.mapping
