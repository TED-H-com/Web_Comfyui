import copy
import json
from pathlib import Path

import pytest

import community_workflows as cw


@pytest.fixture
def catalog():
    return json.loads((cw.ROOT / cw.CATALOG).read_text())


@pytest.fixture
def selected(catalog):
    entry = next(e for e in catalog["workflows"] if e["distribution"] == "vendored")
    return entry, catalog["sources"][entry["source"]]


def test_entire_vendored_collection_and_reference_policy(catalog):
    assert len(catalog["workflows"]) == 233
    vendored = [e for e in catalog["workflows"] if e["distribution"] == "vendored"]
    assert len(vendored) == 18
    for entry in vendored:
        assert cw.validate_entry(cw.ROOT, entry, catalog["sources"][entry["source"]]) == []
    for entry in catalog["workflows"]:
        if entry["source"] == "ecjojo":
            assert entry["distribution"] == "links-only"
            assert "local_path" not in entry
        assert catalog["sources"][entry["source"]]["commit"] in entry["upstream_url"]


def test_links_only_refused_before_fetch(tmp_path, catalog, monkeypatch):
    entry = next(e for e in catalog["workflows"] if e["source"] == "ecjojo")
    monkeypatch.setattr(cw, "fetch", lambda *args: pytest.fail("must not fetch unlicensed source"))
    with pytest.raises(ValueError, match="redistribution"):
        cw.import_entry(tmp_path, entry, catalog["sources"]["ecjojo"])


def test_source_hash_checked_before_parse(tmp_path, selected):
    entry, source = selected
    path = tmp_path / entry["upstream_path"]
    path.parent.mkdir(parents=True)
    path.write_text("tampered")
    with pytest.raises(ValueError, match="SHA-256"):
        cw.fetch(source, entry["upstream_path"], entry["upstream_sha256"], tmp_path)


@pytest.mark.parametrize("path", ["../escape.json", "/absolute.json", "nested/../../escape.json"])
def test_path_traversal_blocked(tmp_path, path):
    with pytest.raises(ValueError, match="unsafe"):
        cw.contained(tmp_path, path)


def test_symlink_escape_blocked(tmp_path):
    inside = tmp_path / "inside"
    outside = tmp_path / "outside"
    inside.mkdir()
    outside.mkdir()
    (inside / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        cw.contained(inside, "link/file.json")


def test_existing_user_edits_preserved(tmp_path):
    path = tmp_path / "workflow.json"
    path.write_bytes(b"user changes")
    with pytest.raises(ValueError, match="modified file"):
        cw.write_unmodified(path, b"upstream version")
    assert path.read_bytes() == b"user changes"


def test_sanitizer_removes_media_paths_and_credentials_but_keeps_public_urls():
    value = {"author": "dseditor", "url": "https://huggingface.co/example/model", "api_key": "private",
             "windows": "C:\\Users\\person\\file.png", "unix": "/home/person/a.png",
             "videoPreview": {"params": {"fullpath": "/home/person/video.mp4"}},
             "data": "data:image/png;base64,abcdef", "token": "hf_" + "x" * 32,
             "model": "Flux\\model.gguf", "autoip": "aip_" + "x" * 32}
    result = cw.scrub(value)
    cw.safety_check(result)
    assert result["author"] == "dseditor"
    assert result["url"].startswith("https://")
    assert result["api_key"] == ""
    assert "videoPreview" not in result
    assert result["model"] == "Flux/model.gguf"
    assert result["autoip"] == "[credential removed]"


def test_dangling_and_stale_links_rejected(selected):
    entry, _ = selected
    original = json.loads((cw.ROOT / entry["local_path"]).read_text())
    broken = copy.deepcopy(original)
    broken["links"][0][1] = -100
    with pytest.raises(ValueError, match="dangling"):
        cw.validate_graph(broken)
    broken = copy.deepcopy(original)
    broken["links"].pop()
    with pytest.raises(ValueError, match="stale"):
        cw.validate_graph(broken)


def test_saved_object_info_reports_missing_nodes(selected):
    entry, source = selected
    missing = cw.validate_entry(cw.ROOT, entry, source, {})
    assert missing
    info = {name: {} for name in entry["node_types"]}
    assert cw.validate_entry(cw.ROOT, entry, source, info) == []


def test_import_reproducible_and_preserves_license(tmp_path, selected, monkeypatch):
    entry, source = selected
    # A sanitized source fixture exercises importing without network access.
    original = json.loads((cw.ROOT / entry["local_path"]).read_text())
    entry = dict(entry)
    raw = cw.encode(original)
    entry["upstream_sha256"] = cw.digest(raw)
    entry["local_sha256"] = cw.digest(cw.encode(cw.sanitize(original, entry, source)))
    license_data = (cw.ROOT / source["license_local_path"]).read_bytes()
    def fake_fetch(_source, path, expected, source_dir):
        data = license_data if path == "LICENSE" else raw
        assert cw.digest(data) == expected
        return data
    monkeypatch.setattr(cw, "fetch", fake_fetch)
    first = cw.import_entry(tmp_path, entry, source)
    assert cw.import_entry(tmp_path, entry, source) == first
    assert cw.validate_entry(tmp_path, entry, source) == []
    assert (tmp_path / source["license_local_path"]).read_bytes() == license_data
