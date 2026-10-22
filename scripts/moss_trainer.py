#!/usr/bin/env python3
"""KohakuMoss integration: offline config/preflight and explicit pinned downloads.

Standard-library only. Never imports torch, starts training, or changes ComfyUI.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys

LOCK = Path(__file__).resolve().parents[1] / "manifests/moss/upstream.lock.json"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


def read_lock():
    return json.loads(LOCK.read_text(encoding="utf-8"))


def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def absolute(value):
    return Path(value).expanduser().resolve()


def selected_models(lock, variant):
    return [f for f in lock["models"]["files"]
            if not f["path"].startswith("diffusion_models/")
            or f"moss_image_{variant}_" in f["path"]]


def model_commands(lock, weights, variant):
    return [["hf", "download", lock["models"]["repository"],
             *[f["path"] for f in selected_models(lock, variant)],
             "--revision", lock["models"]["revision"], "--local-dir", str(weights)]]


def setup_commands(lock, trainer):
    return [["git", "init", str(trainer)],
            ["git", "-C", str(trainer), "remote", "add", "origin", lock["trainer"]["repository"]],
            ["git", "-C", str(trainer), "fetch", "--depth", "1", "origin", lock["trainer"]["revision"]],
            ["git", "-C", str(trainer), "checkout", "--detach", "FETCH_HEAD"]]


def training_commands(trainer, config):
    kogine = str(trainer / ".venv/bin/kogine")
    data = config.with_name(config.stem + "_data.py")
    return [[str(trainer / ".venv/bin/kohakumoss-config"), "check", str(config)],
            *[[kogine, "run", str(trainer / "scripts/data" / script), "--config", str(data)]
              for script in ("inspect_data.py", "cache_text.py", "cache_latents.py")],
            [kogine, "run", str(trainer / "scripts/train/moss.py"), "--config", str(config)]]


def config_text(args, lock):
    """Emit Python literals with repr: paths/captions never become executable text."""
    recipe = str(absolute(args.trainer) / lock["trainer"]["recipe"])
    knobs = {
        "WEIGHTS": str(absolute(args.weights)), "VARIANT": args.variant,
        "DATASETS": [{"source": "folder", "path": str(absolute(args.dataset)), "txt_as": "nl"}],
        "CAPTION": {"template": "nl"}, "RUN_NAME": args.name,
        "STEPS": args.steps, "DIM": args.rank, "ALPHA": float(args.rank),
        "LR": args.learning_rate, "SAVE_FORMAT": "comfy", "SAVE_MERGED": False,
        "SAVE_EVERY": min(args.steps, 250), "KEEP_LAST": 3,
        "SAMPLE_EVERY": 0, "LATENT_CACHE": "offline", "TEXT_CACHE": "offline",
        "CACHE_DIR": str(absolute(args.cache)), "SEED": args.seed,
        "SCHEDULE": {"mode": "cosine", "warmup": min(100, args.steps // 10),
                     "end": -1, "min_value": 0.1},
    }
    return ('# KohakuMoss integration config; generated without loading weights.\n'
            'from kohakuengine import use_config\n\n'
            f'use_config({recipe!r})\n\n' +
            "".join(f"{key} = {value!r}\n" for key, value in knobs.items()))


def data_config_text(args, config):
    weights = str(absolute(args.weights))
    return ('# KohakuMoss offline cache configuration.\n'
            'from kohakuengine import use_config\n\n'
            f'use_config({str(config)!r})\n'
            'TEXT_ENCODER = "kohakumoss.encoders.text_encoder"\n'
            f'TEXT_ENCODER_KWARGS = {{"path": {weights!r}, "device": "cuda"}}\n'
            'IMAGE_ENCODER = "kohakumoss.encoders.vae_encoder"\n'
            f'IMAGE_ENCODER_KWARGS = {{"path": {weights!r}, "device": "cuda"}}\n')


def preflight(args, lock):
    """Filesystem checks only; metadata validation is not a GPU qualification."""
    problems = []
    trainer, weights, dataset = map(absolute, (args.trainer, args.weights, args.dataset))
    revision = None
    if (trainer / ".git").exists() and shutil.which("git"):
        result = subprocess.run(["git", "-C", str(trainer), "rev-parse", "HEAD"],
                                capture_output=True, text=True, check=False)
        revision = result.stdout.strip()
    if revision != lock["trainer"]["revision"]:
        problems.append("Trainer checkout absent or revision differs from lock.")
    license_path = trainer / "LICENSE"
    if not license_path.is_file() or digest(license_path) != lock["trainer"]["license_sha256"]:
        problems.append("Trainer LICENSE absent or differs from reviewed license.")
    for relative in (lock["trainer"]["recipe"], "scripts/train/moss.py", ".venv/bin/kogine"):
        if not (trainer / relative).is_file():
            problems.append(f"Missing trainer file: {relative}")
    models = []
    for entry in selected_models(lock, args.variant):
        path = weights / entry["path"]
        valid = path.is_file() and path.stat().st_size == entry["bytes"]
        if valid and args.hash_models:
            valid = digest(path) == entry["sha256"]
        models.append({"path": entry["path"], "valid": valid,
                       "check": "size+sha256" if args.hash_models else "size-only"})
        if not valid:
            problems.append(f"Model missing or mismatched: {entry['path']}")
    images = sorted(p for p in dataset.rglob("*") if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES) if dataset.is_dir() else []
    missing = [p.name for p in images if not p.with_suffix(".txt").is_file()
               or not p.with_suffix(".txt").read_text(encoding="utf-8").strip()]
    if not images:
        problems.append("Dataset contains no supported image files.")
    if missing:
        problems.append(f"{len(missing)} images lack nonempty UTF-8 .txt captions.")
    return {"feature": "KohakuMoss training integration", "ready": not problems,
            "qualification": "filesystem-only; GPU training and ComfyUI loading untested",
            "trainer_revision": revision, "models": models,
            "dataset_images": len(images), "missing_captions": missing, "problems": problems}


def positive(value):
    number = int(value)
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("plan", "config", "preflight", "setup", "models"))
    p.add_argument("--trainer", default="~/tools/KohakuMossTrainer")
    p.add_argument("--weights", default="~/ComfyUI/models")
    p.add_argument("--dataset", default="~/datasets/moss-character")
    p.add_argument("--cache", default="~/agent-work/moss-cache")
    p.add_argument("--name", default="kohaku-character-pilot")
    p.add_argument("--variant", choices=("base", "sft"), default="base")
    p.add_argument("--steps", type=positive, default=30)
    p.add_argument("--rank", type=positive, default=32)
    p.add_argument("--learning-rate", type=float, default=1e-4)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--output", help="write config and sibling _data.py; default prints config")
    p.add_argument("--hash-models", action="store_true", help="read model bytes for SHA256; no GPU use")
    p.add_argument("--execute", action="store_true", help="setup/models only; default is dry run")
    p.add_argument("--allow-downloads", action="store_true")
    p.add_argument("--accept-upstream-license", action="store_true")
    p.add_argument("--accept-model-license", action="store_true")
    return p


def main(argv=None):
    p = parser()
    args = p.parse_args(argv)
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.name):
        p.error("--name must be 1-80 letters, digits, underscores or hyphens")
    if not 0 < args.learning_rate <= 1:
        p.error("--learning-rate must be in (0, 1]")
    if args.execute and args.command not in ("setup", "models"):
        p.error("--execute is only supported for setup/models; training is never executed")
    lock = read_lock()
    trainer, weights = absolute(args.trainer), absolute(args.weights)
    config = absolute(args.output) if args.output else trainer / "configs/user" / (args.name + ".py")
    try:
        if args.command == "preflight":
            report = preflight(args, lock)
            print(json.dumps(report, indent=2))
            return 0 if report["ready"] else 1
        if args.command == "config":
            content = config_text(args, lock)
            if args.output:
                data = config.with_name(config.stem + "_data.py")
                if config.exists() or data.exists():
                    raise ValueError("Refusing to overwrite existing config files.")
                config.parent.mkdir(parents=True, exist_ok=True)
                with config.open("x", encoding="utf-8") as f:
                    f.write(content)
                with data.open("x", encoding="utf-8") as f:
                    f.write(data_config_text(args, config))
                print(json.dumps({"config": str(config), "data_config": str(data),
                                  "commands": training_commands(trainer, config)}, indent=2))
            else:
                print(content, end="")
            return 0
        if args.command in ("setup", "models"):
            commands = setup_commands(lock, trainer) if args.command == "setup" else model_commands(lock, weights, args.variant)
            if args.execute:
                accepted = args.accept_upstream_license if args.command == "setup" else args.accept_model_license
                if not args.allow_downloads or not accepted:
                    raise ValueError("Execution requires --allow-downloads and the matching --accept-*-license flag.")
                if args.command == "setup" and trainer.exists():
                    raise ValueError("Trainer destination already exists; refusing to change it.")
                if args.command == "models":
                    for entry in selected_models(lock, args.variant):
                        existing = weights / entry["path"]
                        if existing.exists() and (not existing.is_file()
                                or existing.stat().st_size != entry["bytes"]
                                or digest(existing) != entry["sha256"]):
                            raise ValueError(f"Existing model differs from lock; refusing to overwrite: {entry['path']}")
                for cmd in commands:
                    subprocess.run(cmd, check=True)
                if args.command == "setup":
                    if digest(trainer / "LICENSE") != lock["trainer"]["license_sha256"]:
                        raise ValueError("Downloaded LICENSE differs from reviewed lock.")
                else:
                    for entry in selected_models(lock, args.variant):
                        if digest(weights / entry["path"]) != entry["sha256"]:
                            raise ValueError(f"Downloaded model hash mismatch: {entry['path']}")
            print(json.dumps({"executed": args.execute, "commands": commands,
                              "note": "Setup checks out source only. Dependencies and training are separate opt-in commands."}, indent=2))
            return 0
        print(json.dumps({"feature": lock["feature"], "dry_run": True,
                          "trainer": lock["trainer"],
                          "setup_commands": setup_commands(lock, trainer),
                          "dependency_install_command": f"cd {shlex.quote(str(trainer))} && bash scripts/install/install.sh",
                          "model_commands": model_commands(lock, weights, args.variant),
                          "model_download_bytes": sum(f["bytes"] for f in selected_models(lock, args.variant)),
                          "config_path": str(config), "training_commands": training_commands(trainer, config),
                          "qualification": lock["qualification"]}, indent=2))
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"KohakuMoss integration: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
