from pathlib import Path
from scripts.uupd.diff import load_known, save_known, diff_new

def test_first_run_treats_everything_as_new(tmp_path: Path):
    state = tmp_path / "known.json"
    diff = diff_new(state, [{"uuid": "a"}, {"uuid": "b"}])
    assert {d["uuid"] for d in diff} == {"a", "b"}

def test_subsequent_run_emits_only_new(tmp_path: Path):
    state = tmp_path / "known.json"
    save_known(state, [{"uuid": "a"}])
    diff = diff_new(state, [{"uuid": "a"}, {"uuid": "b"}])
    assert {d["uuid"] for d in diff} == {"b"}

def test_known_file_roundtrip(tmp_path: Path):
    state = tmp_path / "known.json"
    data = [{"uuid": "x", "title": "T"}]
    save_known(state, data)
    assert load_known(state) == data
