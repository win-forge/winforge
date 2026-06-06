from pathlib import Path
from scripts.lib.yaml import load

def test_loads_valid(tmp_path: Path):
    p = tmp_path / "a.yaml"
    p.write_text("foo: 1\nbar: [a, b]\n")
    assert load(p) == {"foo": 1, "bar": ["a", "b"]}

def test_empty_file_returns_empty_dict(tmp_path: Path):
    p = tmp_path / "empty.yaml"
    p.write_text("")
    assert load(p) == {}

def test_malformed_raises(tmp_path: Path):
    import pytest
    p = tmp_path / "bad.yaml"
    p.write_text("foo: [unterminated")
    with pytest.raises(SystemExit):
        load(p)
