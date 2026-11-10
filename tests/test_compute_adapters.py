"""Offline Studio-to-PRISM contracts; files are synthetic disk fixtures only."""
import base64
import copy
from pathlib import Path

import pytest

from webui import compute_adapters as A
from scripts.colab_remote.remote_job import validate_manifest


IDS = {"request_id": "00000000-0000-4000-8000-000000000001",
       "account_id": "00000000-0000-4000-8000-000000000002"}
SECTIONS = {"integrated_multimodal_description": "A blue balloon rises.",
            "overall_soundscape": "Wind.", "non_diegetic_music": "None."}
REF_SECTIONS = {"subject_definitions": "<Picture 1> is the hero.", "summary": "The hero waves.",
                "retention_analysis": "Keep the illustrated design.", "detailed_description": "A slow wave.",
                "overall_soundscape": "Wind.", "non_diegetic_music": "None."}


def make_file(tmp_path, name="test.png", data=b"synthetic fixture"):
    path = tmp_path / name
    path.write_bytes(data)
    return path


def assert_worker_accepts(body, root):
    """Reproduce ColabService's exact body-to-manifest wiring, no provider."""
    import hashlib
    inputs, inventory = [], []
    for index, entry in enumerate(body["files"]):
        raw = base64.b64decode(entry["data"], validate=True)
        name = f"input/{index+1}" + Path(entry["name"]).suffix
        inputs.append(name)
        inventory.append({"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
    job = {**body["job"], "id": "output"}
    if job.get("mode") == "frames":
        job["first"] = inputs[0]
        if len(inputs) > 1:
            job["last"] = inputs[1]
    else:
        job["inputs"] = inputs
    return validate_manifest({"schema_version": 1, "job_id": body["request_id"],
        "deadline_epoch": 10000, "stop_on_error": True, "jobs": [job],
        "input_files": inventory}, root, check_files=False)


@pytest.mark.parametrize("gpu", ["L4", "G4", "A100", "H100"])
def test_text_image_exact_square_and_actual_gpu_options(tmp_path, gpu):
    from webui.colab_service import GPU_TYPES
    assert gpu in GPU_TYPES
    body = A.build_colab_request("qwen21-text2img", {"positive": "中文海報", "aspectRatio": "1:1",
        "megapixels": "1", "seed": "18446744073709551615"}, {}, gpu=gpu, **IDS)
    assert body["job"] == {"type": "image", "prompt": "中文海報", "aspect": "1:1", "size": "1K", "seed": 2**64-1}
    assert body["files"] == []
    assert_worker_accepts(body, tmp_path)


@pytest.mark.parametrize("settings,code", [
    ({"positive": "x", "negative": "remove text"}, "negative_unsupported"),
    ({"positive": "x", "steps": 25}, "engine_option_unsupported"),
    ({"positive": "x", "cfg": True}, "engine_option_unsupported"),
    ({"positive": "x", "batchSize": 2}, "engine_option_unsupported"),
    ({"positive": "x", "useDopsd": True}, "engine_option_unsupported"),
    ({"positive": "x", "unexpected": 1}, "setting_unsupported"),
    ({"positive": "x", "aspect": "1088x1920"}, "geometry_unsupported"),
    ({"positive": "x", "seed": 2**64}, "seed_invalid"),
    ({"positive": "x", "seed": True}, "seed_invalid"),
])
def test_no_silent_setting_loss(settings, code):
    result = A.colab_compatibility("text2img", settings)
    assert result["supported"] is False
    assert result["code"] == code


def test_normalisation_only_known_defaults_no_mutation():
    source = {"positive": "x", "steps": 25, "cfg": 1, "negative": "", "megapixels": "1", "aspectRatio": "1:1"}
    before = copy.deepcopy(source)
    normalized, warnings = A.normalise_intent("qwen21-text2img", source)
    assert source == before
    assert normalized == {"positive": "x", "megapixels": "1", "aspectRatio": "1:1"}
    assert warnings and "steps" in warnings[0]
    assert A.colab_compatibility("qwen21-text2img", normalized)["supported"]
    custom = {"positive": "x", "negative": "watermark", "steps": 30, "cfg": 2}
    assert A.normalise_intent("qwen21-text2img", custom) == (custom, [])
    boolean = {"positive": "x", "batchSize": True}
    assert A.normalise_intent("text2img", boolean) == (boolean, [])
    assert not A.colab_compatibility("text2img", boolean)["supported"]


def test_actual_aspect_widget_width_height_preserve_exact_canvas():
    result = A.build_colab_request("text2img", {"positive": "blue square", "width": 1024, "height": 1024}, {}, **IDS)
    assert result["job"]["size"] == "1K" and result["job"]["aspect"] == "1:1"
    for geometry in ({"width": 1088, "height": 1920}, {"width": 1024},
                     {"width": True, "height": 1024}, {"width": 1024, "height": 1024, "size": "2K"}):
        assert not A.colab_compatibility("text2img", {"positive": "x", **geometry})["supported"]


def test_custom_h3_options_preserved_and_rejected():
    settings = {**SECTIONS, "aspect": "landscape-preview", "quality": "quality", "acceleration": "sage"}
    normalized, warnings = A.normalise_intent("h3-text-video", settings)
    assert normalized["frames"] == 124 and normalized["acceleration"] == "sage"
    assert warnings and A.colab_compatibility("h3-text-video", normalized)["code"] == "engine_option_unsupported"


def test_qwen_reference_order_identity_style_and_original_bytes(tmp_path):
    image1 = make_file(tmp_path, "first.png", b"first original")
    image2 = make_file(tmp_path, "second.png", b"second original")
    body = A.build_colab_request("qwen21-edit", {"instruction": "Use <image2> style on <image1>.", "resolution": "1024"},
                                {"image2": image2, "image": image1}, **IDS)
    assert [base64.b64decode(f["data"]) for f in body["files"]] == [b"first original", b"second original"]
    assert body["job"]["prompt"].index("<Picture 1>") < body["job"]["prompt"].index("<Picture 2>")
    assert "Use <Picture 2> style on <Picture 1>." in body["job"]["prompt"]
    from scripts.colab_remote import h3graph as G
    media = [{"name": "first.png", "kind": "image", "width": 1024, "height": 1024},
             {"name": "second.png", "kind": "image", "width": 1024, "height": 1024}]
    plan = G.plan_picture({**body["job"], "seed": 12}, media)
    graph = G.picture_graph(G.load_template(G.PICTURE_TEMPLATE), plan)
    assert graph["ir1"]["inputs"]["image"] == "first.png"
    assert graph["ir2"]["inputs"]["image"] == "second.png"
    assert graph[G.N_IMG_ENC]["inputs"]["images.image_1"] == ["ir1", 0]
    assert_worker_accepts(body, tmp_path)
    portrait = A.build_colab_request("character-photoshoot", {"instruction": "黃昏海邊", "visualStyle": "anime",
        "_characterVisualClass": "anime", "_characterGender": "female", "_characterAppearance": "synthetic profile",
        "resolution": "1024"}, {"image": image1}, **IDS)
    assert "全畫面採用一致的動漫畫風" in portrait["job"]["prompt"]
    assert "保持臉型、五官比例" in portrait["job"]["prompt"]
    assert "黃昏海邊" in portrait["job"]["prompt"]
    assert "_characterGender" not in str(portrait) and "_characterAppearance" not in str(portrait)
    assert_worker_accepts(portrait, tmp_path)


@pytest.mark.parametrize("feature,settings,slots", [
    ("qwen21-edit", {"instruction": "x"}, ["image2"]),
    ("qwen21-edit", {"instruction": "x"}, ["image", "image3"]),
    ("character-photoshoot", {"instruction": "x"}, ["image", "image2"]),
    ("text2img", {"positive": "x"}, ["image"]),
    ("h3-image-video", {"prompt": "x"}, ["last_frame"]),
    ("h3-reference-video", {"prompt": "@Image1"}, ["reference2"]),
    ("h3-reference-video", {"prompt": "@Image1"}, ["reference", "reference2"]),
    ("h3-reference-video", {"prompt": "@Image1"}, ["reference", *[f"reference{i}" for i in range(2, 10)]]),
])
def test_unsupported_input_layouts(feature, settings, slots):
    assert not A.colab_compatibility(feature, settings, slots)["supported"]


def test_character_style_conflict_fails_closed():
    assert A.colab_compatibility("character-photoshoot", {"instruction": "x", "visualStyle": "realistic",
        "_characterVisualClass": "anime"}, ["image"])["code"] == "style_conflict"


@pytest.mark.parametrize("frames", list(range(124, 363, 17)))
def test_legacy_frame_grid_preserved(tmp_path, frames):
    body = A.build_colab_request("h3-text-video", {**SECTIONS, "aspect": "landscape-preview", "frames": str(frames)}, {}, **IDS)
    seconds = body["job"]["seconds"]
    raw = max(5, round(seconds * 24))
    assert raw + (5 - raw % 17) % 17 == frames
    assert body["job"]["resolution"] == "480P"
    assert "overall_soundscape:\nWind." in body["job"]["prompt"]
    assert_worker_accepts(body, tmp_path)


def test_native_geometry_requires_explicit_prism_setting():
    settings = {**SECTIONS, "aspect": "landscape", "frames": 124}
    assert A.colab_compatibility("h3-text-video", settings)["code"] == "geometry_unsupported"
    assert A.colab_compatibility("h3-text-video", {**settings, "resolution": "720P"})["supported"]
    assert not A.colab_compatibility("h3-text-video", {**settings, "aspect": "square-preview"})["supported"]


def test_character_h3_reuses_guardrails_and_motion(tmp_path):
    image = make_file(tmp_path)
    settings = {"description": "The hero smiles.", "motionProfile": "gentle", "visualStyle": "anime",
                "aspect": "landscape", "resolution": "480P", "frames": 124}
    body = A.build_colab_request("h3-image-video", settings, {"image": image}, **IDS)
    assert "Keep the exact illustrated identity" in body["job"]["prompt"]
    assert "subtle hand-drawn character motion" in body["job"]["prompt"]
    assert body["job"]["mode"] == "frames"
    assert_worker_accepts(body, tmp_path)


@pytest.mark.parametrize("style,prose", [("anime", "consistent 2D anime illustration style"),
                                       ("realistic", "consistent realistic photographic style")])
@pytest.mark.parametrize("feature,sections,slots", [
    ("h3-text-video", SECTIONS, []),
    ("h3-image-video", SECTIONS, ["image", "last_frame"]),
    ("h3-reference-video", REF_SECTIONS, ["reference"]),
])
def test_generic_h3_style_preserves_sections_and_inputs(tmp_path, style, prose, feature, sections, slots):
    settings = {**sections, "visualStyle": style, "aspect": "landscape-preview", "frames": 124}
    before = copy.deepcopy(settings)
    files = {slot: make_file(tmp_path, slot + ".png", slot.encode()) for slot in slots}
    body = A.build_colab_request(feature, settings, files, **IDS)
    assert settings == before
    prompt = body["job"]["prompt"]
    assert prose in prompt
    assert all(value in prompt for value in sections.values())
    assert [base64.b64decode(item["data"]) for item in body["files"]] == [slot.encode() for slot in slots]
    assert_worker_accepts(body, tmp_path)


@pytest.mark.parametrize("style", ["anime", "realistic"])
def test_generic_h3_free_prompt_style_is_not_character_binding(tmp_path, style):
    body = A.build_colab_request("h3-text-video", {"prompt": "An adult waves.", "visualStyle": style}, {}, **IDS)
    assert "An adult waves." in body["job"]["prompt"]
    assert ("anime illustration" if style == "anime" else "realistic photographic") in body["job"]["prompt"]
    assert_worker_accepts(body, tmp_path)
    for context in ({"_characterId": "owned"}, {"_characterVisualClass": style}, {"_characterReference": {}}):
        result = A.colab_compatibility("h3-image-video", {"prompt": "An adult waves.", "visualStyle": style, **context}, ["image"])
        assert result["code"] == "character_prompt_invalid"


@pytest.mark.parametrize("style,motion", [("anime", "gentle"), ("realistic", "natural")])
@pytest.mark.parametrize("feature,slot", [("h3-image-video", "image"), ("h3-reference-video", "reference")])
def test_bound_h3_still_matches_character_compiler_exactly(tmp_path, style, motion, feature, slot):
    settings = {"description": "The adult hero smiles.", "motionProfile": motion, "visualStyle": style,
                "_characterId": "synthetic-owned", "_characterVisualClass": style,
                "aspect": "landscape", "resolution": "480P", "frames": 124}
    body = A.build_colab_request(feature, settings, {slot: make_file(tmp_path)}, **IDS)
    canonical = {**settings, "seed": 0, "acceleration": "stock"}
    expected = A.H3.compile_product_prompt(feature_id=feature, visual_class=style,
        settings=canonical, uploads={slot: slot + ".png"}, output_token="adapter")
    assert body["job"]["prompt"] == expected["5"]["inputs"]["prompt"]
    assert_worker_accepts(body, tmp_path)
    structured = REF_SECTIONS if feature == "h3-reference-video" else SECTIONS
    assert A.colab_compatibility(feature, {**settings, **structured}, [slot])["code"] == "prompt_conflict"
    conflict = {**settings, "visualStyle": "realistic" if style == "anime" else "anime"}
    assert A.colab_compatibility(feature, conflict, [slot])["code"] == "style_conflict"


def test_first_last_order_and_ref_kind_order(tmp_path):
    first = make_file(tmp_path, "first.png", b"first")
    last = make_file(tmp_path, "last.png", b"last")
    body = A.build_colab_request("h3-image-video", {**SECTIONS, "aspect": "landscape-preview", "frames": 124},
        {"last_frame": last, "image": first}, **IDS)
    assert [base64.b64decode(x["data"]) for x in body["files"]] == [b"first", b"last"]
    manifest = assert_worker_accepts(body, tmp_path)
    assert manifest["jobs"][0]["first"] == "input/1.png" and manifest["jobs"][0]["last"] == "input/2.png"
    video = make_file(tmp_path, "video.mp4", b"video")
    audio = make_file(tmp_path, "audio.wav", b"audio")
    body = A.build_colab_request("h3-reference-video", {"prompt": "@Image1 follows @Video1 and @Audio1.",
        "aspect": "landscape-preview"}, {"audio1": audio, "video1": video, "reference": first}, **IDS)
    assert [base64.b64decode(x["data"]) for x in body["files"]] == [b"first", b"video", b"audio"]
    assert_worker_accepts(body, tmp_path)


def test_local_video_embedded_audio_tags_not_silently_reassigned():
    settings = {**REF_SECTIONS, "overall_soundscape": "Use <Audio 1> from the clip.",
                "detailed_description": "Follow <Video 1>.", "aspect": "landscape-preview", "frames": 124}
    result = A.colab_compatibility("h3-reference-video", settings, ["reference", "video1", "audio1"])
    assert result["code"] == "video_audio_reference_unsupported"


def test_references_cannot_be_dropped_or_invented():
    assert not A.colab_compatibility("qwen21-edit", {"instruction": "Use <image4>"}, ["image"])["supported"]
    assert not A.colab_compatibility("h3-text-video", {"prompt": "Use @Image1"})["supported"]
    assert not A.colab_compatibility("h3-image-video", {"prompt": "Use <Picture 2>"}, ["image"])["supported"]
    assert not A.colab_compatibility("h3-text-video", {**SECTIONS, "subject_definitions": "x"})["supported"]


def test_compatibility_does_not_open_files(monkeypatch):
    monkeypatch.setattr(A.os, "open", lambda *a, **k: pytest.fail("compatibility read a file"))
    result = A.colab_compatibility("character-photoshoot", {"instruction": "x"}, ["image"])
    assert result["supported"] and result["recipe_id"] == A.RECIPE_ID
    assert "prompt" not in result["output_settings"]


@pytest.mark.parametrize("path_kind", ["relative", "directory", "symlink", "empty", "wrong_kind", "missing"])
def test_invalid_local_file_fails_closed(tmp_path, path_kind):
    path = make_file(tmp_path)
    if path_kind == "relative":
        path = Path("relative.png")
    elif path_kind == "directory":
        path = tmp_path / "directory.png"
        path.mkdir()
    elif path_kind == "symlink":
        link = tmp_path / "link.png"
        link.symlink_to(path)
        path = link
    elif path_kind == "empty":
        path.write_bytes(b"")
    elif path_kind == "wrong_kind":
        path = make_file(tmp_path, "audio.wav")
    elif path_kind == "missing":
        path = tmp_path / "missing.png"
    with pytest.raises(A.AdapterError) as error:
        A.build_colab_request("img2img", {"instruction": "x"}, {"image": path}, **IDS)
    assert str(tmp_path) not in str(error.value)


def test_aggregate_size_cap_precedes_read_of_next_file(tmp_path, monkeypatch):
    monkeypatch.setattr(A, "MAX_INPUT_BYTES", 10)
    first = make_file(tmp_path, "first.png", b"123456")
    second = make_file(tmp_path, "second.png", b"12345")
    with pytest.raises(A.AdapterError, match="32 MiB"):
        A.build_colab_request("qwen21-edit", {"instruction": "x"}, {"image": first, "image2": second}, **IDS)


@pytest.mark.parametrize("extra", [{"max_units": True}, {"max_units": float("nan")}, {"max_minutes": 4},
                                   {"max_minutes": 5.5}, {"gpu": "T4"}, {"request_id": "../bad"}])
def test_invalid_budget_or_identity_rejected_before_file_read(tmp_path, monkeypatch, extra):
    monkeypatch.setattr(A, "_read_owned", lambda *a: pytest.fail("read before budget validation"))
    with pytest.raises(A.AdapterError):
        A.build_colab_request("img2img", {"instruction": "x"}, {"image": tmp_path / "image.png"}, **{**IDS, **extra})


def test_unknown_feature_not_auto_converted():
    assert A.colab_compatibility("dance", {"prompt": "dance"})["code"] == "feature_unsupported"


@pytest.mark.parametrize("feature,settings", [
    ("text2img", {"positive": "x", "aspect": []}),
    ("text2img", {"positive": "x", "size": []}),
    ("h3-text-video", {"prompt": "x", "resolution": []}),
    ("h3-text-video", {"prompt": "x", "intensity": []}),
])
def test_malformed_settings_return_typed_error(feature, settings):
    with pytest.raises(A.AdapterError):
        A.build_colab_request(feature, settings, {}, **IDS)


def test_changed_file_during_read_fails_closed(tmp_path, monkeypatch):
    path = make_file(tmp_path)
    original = A.os.fstat
    calls = 0

    def changed(fd):
        nonlocal calls
        calls += 1
        record = original(fd)
        if calls == 2:
            from types import SimpleNamespace
            values = {key: getattr(record, key) for key in ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns")}
            values["st_mtime_ns"] += 1
            return SimpleNamespace(**values)
        return record

    monkeypatch.setattr(A.os, "fstat", changed)
    with pytest.raises(A.AdapterError) as error:
        A.build_colab_request("img2img", {"instruction": "x"}, {"image": path}, **IDS)
    assert error.value.code == "file_changed"
