#!/usr/bin/env python3
"""Inspect, reproduce and statically validate the pinned community collection.

Standard library only. Never installs nodes, downloads weights or executes a graph.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
from pathlib import Path
import re
import sys
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CATALOG = Path("manifests/community_workflows/catalog.json")
MAX_BYTES = 2 * 1024 * 1024
MODEL_SUFFIX = re.compile(r"\.(?:safetensors|sft|ckpt|pth|pt|bin|gguf|onnx)$", re.I)
PRIVATE_PATH = re.compile(r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\|/(?:home|Users|mnt|media|tmp|private|root)/|~/)")
SECRET = re.compile(r"(?:sk-[A-Za-z0-9_-]{16,}|aip_[A-Za-z0-9_-]{12,}|hf_[A-Za-z0-9]{16,}|gh[pousr]_[A-Za-z0-9]{16,}|Bearer\s+\S+)")
SECRET_KEY = re.compile(r"(?:^|_)(?:api[_-]?key|access[_-]?token|token|auth(?:orization)?|password|credential|secret)(?:$|_)", re.I)
MEDIA_KEYS = {"images", "imgs", "videopreview", "preview", "thumbnail", "base64", "image_base64"}


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encode(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode()


def contained(root: Path, relative: str) -> Path:
    candidate = root / relative
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"unsafe relative path: {relative}")
    if not candidate.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"path escapes destination: {relative}")
    return candidate


def source_url(source: dict, relative: str, *, raw=False) -> str:
    if not re.fullmatch(r"[\w.-]+/[\w.-]+", source["repository"]):
        raise ValueError("invalid GitHub repository")
    if not re.fullmatch(r"[0-9a-f]{40}", source["commit"]):
        raise ValueError("source must use a full commit SHA")
    if relative.startswith("/") or ".." in Path(relative).parts:
        raise ValueError("invalid upstream path")
    host = "https://raw.githubusercontent.com/" if raw else "https://github.com/"
    infix = "/" if raw else "/blob/"
    return host + source["repository"] + infix + source["commit"] + "/" + urllib.parse.quote(relative)


def fetch(source: dict, relative: str, expected: str, source_dir: Path | None) -> bytes:
    if source_dir is not None:
        data = contained(source_dir, relative).read_bytes()
    else:
        request = urllib.request.Request(source_url(source, relative, raw=True), headers={"User-Agent": "community-workflow-importer/1"})
        with urllib.request.urlopen(request, timeout=30) as response:
            data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"source file exceeds {MAX_BYTES} bytes")
    if digest(data) != expected:
        raise ValueError(f"upstream SHA-256 mismatch: {relative}")
    return data


def scrub(value):
    """Strip opaque media/UI caches and secrets; normalize portable model names."""
    if isinstance(value, dict):
        return {k: ("" if SECRET_KEY.search(k) else scrub(v)) for k, v in value.items() if k.lower() not in MEDIA_KEYS}
    if isinstance(value, list):
        return [scrub(v) for v in value]
    if not isinstance(value, str):
        return value
    if "data:" in value.lower() and ";base64," in value.lower():
        return "[embedded media removed]"
    if SECRET.search(value):
        return "[credential removed]"
    if PRIVATE_PATH.search(value):
        return "[local path removed]"
    if MODEL_SUFFIX.search(value):
        return value.replace("\\", "/")
    return value


def sanitize(original: dict, entry: dict, source: dict) -> dict:
    if any(n.get("type", "").startswith("workflow/") for n in original.get("nodes", [])):
        raise ValueError("unexpanded group nodes require separate review")
    # Extra frequently includes unused subgraphs with hidden prompts and paths.
    # Retain only runtime routing data and frontend version hints for this set.
    result = {k: copy.deepcopy(original[k]) for k in ("id", "revision", "last_node_id", "last_link_id", "nodes", "links", "groups", "version") if k in original}
    result["config"] = {}
    result["extra"] = {k: v for k, v in original.get("extra", {}).items() if k in {"frontendVersion", "workflowRendererVersion", "ue_links", "links_added_by_ue"}}
    result = scrub(result)
    for node in result["nodes"]:
        values = node.get("widgets_values")
        if node["type"] == "LoadImage" and isinstance(values, list) and values:
            values[0] = f"input_{node['id']}.png"
        if node["type"] == "SaveImage" and isinstance(values, list) and values:
            values[0] = "community/" + entry["id"]
        if node["type"] == "LayerUtility: SaveImagePlus" and isinstance(values, list) and len(values) >= 2:
            values[0:2] = ["", entry["id"]]
        if node["type"] == "VHS_VideoCombine" and isinstance(values, dict):
            values["filename_prefix"] = "community/" + entry["id"]
    result["extra"]["community_attribution"] = {
        "author": source["author"], "source": source_url(source, entry["upstream_path"]),
        "source_sha256": entry["upstream_sha256"], "license": source["license"],
        "license_file": source["license_local_path"], "modified": "2026-10-22",
        "changes": "Removed cached metadata/media and private paths; replaced input/output filenames; normalized model path separators. Graph topology and sampler settings retained.",
    }
    return result


def validate_graph(workflow: dict) -> None:
    if not isinstance(workflow.get("nodes"), list) or not isinstance(workflow.get("links"), list):
        raise ValueError("expected ComfyUI UI-format nodes and links lists")
    nodes = {node["id"]: node for node in workflow["nodes"]}
    if len(nodes) != len(workflow["nodes"]):
        raise ValueError("duplicate node IDs")
    links = {}
    targets = set()
    for link in workflow["links"]:
        if not isinstance(link, list) or len(link) != 6:
            raise ValueError("invalid link record")
        lid, source, output, target, inp, _ = link
        if lid in links or (target, inp) in targets:
            raise ValueError("duplicate link ID or multiply connected input")
        if source not in nodes or target not in nodes:
            raise ValueError("dangling link node")
        if not isinstance(output, int) or not isinstance(inp, int) or output < 0 or inp < 0:
            raise ValueError("invalid link slot")
        try:
            out_slot = nodes[source].get("outputs", [])[output]
            in_slot = nodes[target].get("inputs", [])[inp]
        except IndexError as exc:
            raise ValueError("dangling link slot") from exc
        if in_slot.get("link") != lid or lid not in (out_slot.get("links") or []):
            raise ValueError("nonreciprocal link")
        links[lid] = link
        targets.add((target, inp))
    for node in nodes.values():
        if not isinstance(node.get("type"), str) or not node["type"]:
            raise ValueError("missing node type")
        for slot, item in enumerate(node.get("inputs", [])):
            lid = item.get("link")
            if lid is not None and (lid not in links or links[lid][3:5] != [node["id"], slot]):
                raise ValueError("stale input link")
        for slot, item in enumerate(node.get("outputs", [])):
            for lid in item.get("links") or []:
                if lid not in links or links[lid][1:3] != [node["id"], slot]:
                    raise ValueError("stale output link")


def safety_check(value) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key.lower() in MEDIA_KEYS or (SECRET_KEY.search(key) and child):
                raise ValueError("embedded media or secret field remains")
            safety_check(child)
    elif isinstance(value, list):
        for child in value:
            safety_check(child)
    elif isinstance(value, str):
        if PRIVATE_PATH.search(value) or SECRET.search(value) or ("data:" in value.lower() and ";base64," in value.lower()):
            raise ValueError("private path, credential or embedded media remains")


def write_unmodified(path: Path, data: bytes) -> None:
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"refusing to replace modified file: {path}")
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # Exclusive creation also prevents races with a concurrent user edit.
    with path.open("xb") as output:
        output.write(data)


def import_entry(root: Path, entry: dict, source: dict, source_dir: Path | None = None) -> Path:
    if entry["distribution"] != "vendored" or source["license"] != "GPL-3.0":
        raise ValueError("links-only source: redistribution not authorized by catalog")
    if not entry["local_path"].startswith("workflows/community/dseditor/"):
        raise ValueError("unexpected community workflow destination")
    destination = contained(root, entry["local_path"])
    license_path = contained(root, source["license_local_path"])
    license_data = fetch(source, source["license_upstream_path"], source["license_sha256"], source_dir)
    raw = fetch(source, entry["upstream_path"], entry["upstream_sha256"], source_dir)
    result = sanitize(json.loads(raw), entry, source)
    validate_graph(result)
    safety_check(result)
    data = encode(result)
    if digest(data) != entry["local_sha256"]:
        raise ValueError("sanitized SHA-256 mismatch; review sanitizer/catalog changes")
    write_unmodified(license_path, license_data)
    write_unmodified(destination, data)
    return destination


def validate_entry(root: Path, entry: dict, source: dict, object_info: dict | None = None) -> list[str]:
    data = contained(root, entry["local_path"]).read_bytes()
    if digest(data) != entry["local_sha256"]:
        raise ValueError("vendored file SHA-256 mismatch")
    license_data = contained(root, source["license_local_path"]).read_bytes()
    if digest(license_data) != source["license_sha256"]:
        raise ValueError("license SHA-256 mismatch")
    workflow = json.loads(data)
    validate_graph(workflow)
    safety_check(workflow)
    attribution = workflow.get("extra", {}).get("community_attribution", {})
    if attribution.get("source") != source_url(source, entry["upstream_path"]) or attribution.get("license") != source["license"]:
        raise ValueError("missing or incorrect attribution")
    types = sorted({node["type"] for node in workflow["nodes"]})
    if types != entry["node_types"]:
        raise ValueError("catalog node inventory differs from graph")
    virtual = {"Note", "MarkdownNote", "Reroute", "SetNode", "GetNode", "Anything Everywhere3"}
    return [name for name in types if object_info is not None and name not in object_info and name not in virtual]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["list", "show", "import", "validate"])
    parser.add_argument("ids", nargs="*", help="catalog IDs; defaults to every entry")
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source-dir", type=Path, help="offline dseditor source tree; hashes still checked")
    parser.add_argument("--object-info", type=Path, help="saved ComfyUI /object_info JSON; check installed node names without executing")
    args = parser.parse_args(argv)
    try:
        catalog = json.loads((args.root / CATALOG).read_text())
        entries = catalog["workflows"]
        unknown = set(args.ids) - {entry["id"] for entry in entries}
        if unknown:
            raise ValueError("unknown workflow IDs: " + ", ".join(sorted(unknown)))
        if args.ids:
            entries = [entry for entry in entries if entry["id"] in args.ids]
        if args.command == "show":
            print(json.dumps(entries, ensure_ascii=False, indent=2))
            return 0
        info = json.loads(args.object_info.read_text()) if args.object_info else None
        failures = 0
        skipped = 0
        for entry in entries:
            source = catalog["sources"][entry["source"]]
            if args.command == "list":
                print(f"{entry['id']}\t{entry['distribution']}\t{entry['title']}")
                continue
            if entry["distribution"] == "links-only":
                if args.command == "import" and args.ids:
                    raise ValueError(f"{entry['id']}: links-only; no redistribution license found")
                skipped += 1
                if args.ids:
                    print(f"SKIP {entry['id']} (links-only)")
                continue
            if args.command == "import":
                import_entry(args.root, entry, source, args.source_dir)
                print(f"IMPORTED {entry['id']}")
            else:
                missing = validate_entry(args.root, entry, source, info)
                if missing:
                    failures += 1
                    print(f"MISSING NODES {entry['id']}: {', '.join(missing)}")
                else:
                    print(f"VALID {entry['id']} (static checks; GPU execution untested)")
        if skipped:
            print(f"Skipped {skipped} source references (no local JSON distribution).")
        return 1 if failures else 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
