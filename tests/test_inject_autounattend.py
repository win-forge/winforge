from scripts.build.inject_autounattend import render

def test_renders_replacements():
    tpl = "user={{USER}} pass={{PASS}}"
    out = render(tpl, {"USER": "yoav", "PASS": "hunter2"})
    assert out == "user=yoav pass=hunter2"

def test_missing_key_left_as_marker():
    tpl = "user={{USER}} pass={{PASS}}"
    out = render(tpl, {"USER": "yoav"})
    assert "pass={{PASS}}" in out

def test_no_placeholders_unchanged():
    tpl = "static text only"
    out = render(tpl, {"X": "y"})
    assert out == tpl
