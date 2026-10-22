"""CPU-only tests. Run via pytest-disktmp; no trainer or models required."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess

import pytest

import moss_trainer as moss


def test_plan_is_offline_and_pinned(monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("plan executed a command"))
    assert moss.main(["plan"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["dry_run"]
    assert len(result["trainer"]["revision"]) == 40
    assert "--revision" in result["model_commands"][0]
    assert result["model_download_bytes"] == 15411341830
    assert not any(result["qualification"].values())


@pytest.mark.parametrize("command", ["setup", "models"])
def test_downloads_require_both_flags(command, monkeypatch, capsys):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("unguarded download"))
    assert moss.main([command, "--execute"]) == 2
    assert "requires --allow-downloads" in capsys.readouterr().err
    assert moss.main([command, "--execute", "--allow-downloads"]) == 2


def test_setup_refuses_existing_checkout(tmp_path, monkeypatch):
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("modified existing checkout"))
    assert moss.main(["setup", "--trainer", str(tmp_path), "--execute", "--allow-downloads",
                      "--accept-upstream-license"]) == 2


def test_config_is_literal_and_never_overwrites(tmp_path, capsys):
    output = tmp_path / "pilot.py"
    dataset = str(tmp_path / "a'; raise RuntimeError('injection') #")
    args = ["config", "--dataset", dataset, "--output", str(output)]
    assert moss.main(args) == 0
    tree = ast.parse(output.read_text())
    assigned = {node.targets[0].id: ast.literal_eval(node.value)
                for node in tree.body if isinstance(node, ast.Assign)}
    assert assigned["DATASETS"][0]["path"] == dataset
    assert assigned["STEPS"] == 30
    assert assigned["SAVE_FORMAT"] == "comfy"
    assert assigned["SCHEDULE"]["warmup"] == 3
    assert assigned["SAMPLE_EVERY"] == 0
    ast.parse((tmp_path / "pilot_data.py").read_text())
    before = output.read_bytes()
    assert moss.main(args) == 2
    assert output.read_bytes() == before


def test_invalid_name_and_learning_rate():
    for values in (["--name", "../escape"], ["--learning-rate", "nan"], ["--steps", "0"]):
        with pytest.raises(SystemExit):
            moss.main(["config", *values])


def test_model_selection_never_mixes_base_and_sft():
    lock = moss.read_lock()
    for variant in ("base", "sft"):
        files = moss.selected_models(lock, variant)
        assert len(files) == 3
        assert sum(f"moss_image_{variant}_" in f["path"] for f in files) == 1


def test_model_download_refuses_existing_different_file(tmp_path, monkeypatch):
    entry = moss.selected_models(moss.read_lock(), "base")[0]
    path = tmp_path / entry["path"]
    path.parent.mkdir(parents=True)
    path.write_bytes(b"existing user model")
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("overwriting user model"))
    assert moss.main(["models", "--weights", str(tmp_path), "--execute", "--allow-downloads",
                      "--accept-model-license"]) == 2
    assert path.read_bytes() == b"existing user model"


def test_preflight_detects_content_and_caption_problems(tmp_path, monkeypatch):
    trainer = tmp_path / "trainer"
    (trainer / ".git").mkdir(parents=True)
    (trainer / "LICENSE").write_text("fixture license")
    for rel in ("configs/recipe.py", "scripts/train/moss.py", ".venv/bin/kogine"):
        target = trainer / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.touch()
    lock = {"trainer": {"revision": "a" * 40, "recipe": "configs/recipe.py",
                        "license_sha256": moss.digest(trainer / "LICENSE")},
            "models": {"files": [{"path": "vae/fixture", "bytes": 4,
                                    "sha256": hashlib.sha256(b"good").hexdigest()}]}}
    model = tmp_path / "models/vae/fixture"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"oops")
    dataset = tmp_path / "data"
    dataset.mkdir()
    (dataset / "image.png").touch()
    args = moss.parser().parse_args(["preflight", "--trainer", str(trainer),
                                    "--weights", str(tmp_path / "models"),
                                    "--dataset", str(dataset), "--hash-models"])
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, "a" * 40, ""))
    monkeypatch.setattr(moss.shutil, "which", lambda name: "/usr/bin/git")
    report = moss.preflight(args, lock)
    assert not report["ready"]
    assert report["missing_captions"] == ["image.png"]
    assert not report["models"][0]["valid"]
    model.write_bytes(b"good")
    (dataset / "image.txt").write_text("A sample caption.")
    assert moss.preflight(args, lock)["ready"]


def test_preflight_missing_everything_is_actionable(tmp_path, capsys):
    assert moss.main(["preflight", "--trainer", str(tmp_path / "missing"),
                      "--dataset", str(tmp_path / "missing"),
                      "--weights", str(tmp_path / "missing")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert len(result["problems"]) >= 5
    assert "filesystem-only" in result["qualification"]


def test_training_cannot_be_executed():
    with pytest.raises(SystemExit):
        moss.main(["plan", "--execute"])


def test_recipe_catalog_has_sources_and_honest_readiness():
    root = Path(__file__).resolve().parents[1]
    catalog = json.loads((root / "manifests/community_recipes/recipes.json").read_text())
    recipes = catalog["recipes"]
    assert len({r["id"] for r in recipes}) == len(recipes)
    assert len(recipes) >= 8
    for recipe in recipes:
        assert recipe["source_urls"]
        assert recipe["status"] == "recipe_only"
        assert recipe["validation"]["gpu_tested"] is False
        assert recipe["validation"]["auto_install"] is False
        assert (root / recipe["documentation"]).is_file()
    frame_gen = next(r for r in recipes if r["id"] == "rtx-video-frame-generation")
    assert frame_gen["settings"]["generation_type"] == "frame rate multiplier"
