"""Actual Web/Colab admission with an isolated, synthetic server namespace.

The server function bodies are compiled from source so importing its deployment
configuration cannot read operator files. Only the catalog/media lookup and
provider boundary are faked; authorization helpers, adapter, service and durable
router execute their real implementations. No GPU or external network is used.
"""
import ast
import base64
import copy
import hashlib
import hmac
import importlib
import io
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import shutil
import types
import time
import uuid

import pytest


ROOT = Path(__file__).resolve().parents[1]


def test_history_api_pages_owned_rows_and_rejects_foreign_cursor(tmp_path, monkeypatch):
    import sys
    import urllib.parse
    monkeypatch.syspath_prepend(str(ROOT / 'webui'))
    from compute_routing import ComputeAdapter, ComputeRouter
    import compute_web
    lane = ComputeAdapter(lambda *args: {'eligible': True, 'queue_depth': 0},
        lambda owner, rid, request, limits: {'state': 'completed', 'downstream_id': rid})
    router = ComputeRouter(tmp_path / 'history.db', {'local': lane}, clock=lambda: 1000.0)
    ids = [str(uuid.UUID(int=index)) for index in range(1, 58)]
    for rid in ids:
        router.submit('owner', rid, {})
    foreign = str(uuid.UUID(int=1000))
    router.submit('another-owner', foreign, {})
    monkeypatch.setattr(compute_web, 'ComputeWeb', lambda handler, server:
        types.SimpleNamespace(owner='owner', user={}, router=router))
    tree = ast.parse((ROOT / 'webui/server.py').read_text())
    method = next(child for node in tree.body if isinstance(node, ast.ClassDef)
                  for child in node.body if isinstance(child, ast.FunctionDef) and child.name == 'api_compute')
    namespace = {'__name__': __name__, 'sys': sys, 'urllib': urllib, 'FEATURE_BY_ID': {},
                 'RequestBodyError': type('RequestBodyError', (Exception,), {})}
    exec(compile(ast.fix_missing_locations(ast.Module(body=[copy.deepcopy(method)], type_ignores=[])),
                 str(ROOT / 'webui/server.py'), 'exec'), namespace)

    def call(query=''):
        responses = []
        handler = types.SimpleNamespace(path='/api/compute/jobs' + query,
            send_json=lambda body, status=200: responses.append((status, body)))
        namespace['api_compute'](handler, '/api/compute/jobs', False)
        return responses[0]

    status, first = call()
    assert status == 200 and len(first['jobs']) == 50
    assert first['next_cursor'] == ids[7]
    status, second = call('?before=' + first['next_cursor'])
    assert status == 200 and [row['request_id'] for row in second['jobs']] == ids[6::-1]
    assert second['next_cursor'] is None
    assert call('?before=' + foreign)[0] == 404
    assert call('?before=' + first['next_cursor'] + '&before=' + foreign)[0] == 400
    assert call('?before=')[0] == 400


def test_history_http_keeps_cursor_query(test_server):
    import urllib.error
    import urllib.request
    base, opener, cookie = test_server
    with opener.open(urllib.request.Request(base + '/api/compute/jobs', headers={'Cookie': cookie}), timeout=15) as response:
        assert 'next_cursor' in json.load(response)
    request = urllib.request.Request(base + '/api/compute/jobs?before=' + str(uuid.uuid4()), headers={'Cookie': cookie})
    with pytest.raises(urllib.error.HTTPError) as error:
        opener.open(request, timeout=15)
    assert error.value.code == 404
    assert json.load(error.value)['error']['code'] == 'compute_request_not_found'


@pytest.mark.parametrize('feature_id', ['character-angles', 'lipsync'])
def test_explicit_anime_real_face_tools_rejected_before_preview_or_dispatch(web_fixture, feature_id):
    f = web_fixture
    f.server.FEATURE_BY_ID[feature_id] = {'id': feature_id, 'builder': 'synthetic'}
    body = {'featureId': feature_id, 'settings': {}, 'files': {}, 'computeVisualClass': 'anime'}
    preview = f.web.router.preview(f.web.owner, body, 'local', {})
    assert all(not candidate['eligible'] and 'visual_class_unsupported' in candidate['reasons'] for candidate in preview['candidates'])
    f.handler._generate_result = lambda *args, **kwargs: pytest.fail('Rejected declared anime must not dispatch')
    with pytest.raises(Exception) as error:
        f.web.submit_local(f.web.owner, str(uuid.uuid4()), body, {})
    assert error.value.code == 'visual_class_unsupported' and not f.stages


@pytest.mark.parametrize('feature_id', ['character-angles', 'lipsync'])
def test_real_face_guard_keeps_undeclared_inputs_and_canonical_bound_visual(web_fixture, feature_id):
    f = web_fixture
    f.server.FEATURE_BY_ID[feature_id] = {'id': feature_id, 'builder': 'synthetic'}
    body = {'featureId': feature_id, 'settings': {}, 'files': {'image': {'name': 'anime-looking-filename.png'}}}
    assert f.web._feature(body)['id'] == feature_id  # No filename/content inference.
    body.update(characterId=f.character['id'], computeVisualClass='realistic')
    with pytest.raises(Exception) as error:
        f.web._feature(body)
    assert error.value.code == 'visual_class_unsupported'  # Canonical character is anime.
    f.character['visualClass'] = 'realistic'
    body['computeVisualClass'] = 'anime'
    assert f.web._feature(body)['id'] == feature_id  # Canonical class remains authoritative.


def load_server_functions(namespace):
    source = ast.parse((ROOT / "webui/server.py").read_text())
    functions = {node.name: node for node in source.body if isinstance(node, ast.FunctionDef)}
    selected = ["strip_submitted_by", "strip_client_character_settings", "validate_generation_file_contract",
                "validate_feature_option_contract", "resolve_server_file", "stage_character_asset_for_comfy",
                "bind_character_capability_files", "character_entry_for_generation", "user_can_see_character",
                "user_can_generate_character", "safe_path_segment", "validate_client_generation_files",
                "plan_colab_generation_request", "record_input_upload_owner", "user_can_use_uploaded_input",
                "stage_colab_uploaded_file", "decode_data_url"]
    nodes = [ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0)]
    nodes.extend(copy.deepcopy(functions[name]) for name in selected)
    handler = next(node for node in source.body if isinstance(node, ast.ClassDef) and
                   any(isinstance(child, ast.FunctionDef) and child.name == "_generate_result" for child in node.body))
    nodes.append(copy.deepcopy(next(node for node in handler.body if isinstance(node, ast.FunctionDef) and node.name == "_generate_result")))
    module = ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[]))
    exec(compile(module, str(ROOT / "webui/server.py"), "exec"), namespace)


@pytest.fixture
def web_fixture(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "webui"))
    web_module = importlib.import_module("compute_web")
    colab_module = importlib.import_module("colab_service")
    qwen = importlib.import_module("qwen_image21")
    from PIL import Image
    buffer = io.BytesIO()
    Image.new("RGB", (8, 8), "blue").save(buffer, "PNG")
    media = buffer.getvalue()
    input_root = tmp_path / "custom-comfy-input"
    input_root.mkdir()
    avatar = tmp_path / "owned-avatar.png"
    avatar.write_bytes(media)

    class Bridge:
        installed = True
        balance = 100
        calls = []

        def __call__(self, operation, directory, **params):
            self.calls.append(operation)
            if operation == "account_identity":
                return {"bound": True, "subject_key": "a" * 64}
            assert operation == "account_status", "no allocation/Google call is permitted in this test"
            return {"authenticated": True, "balance_compute_units": self.balance, "active_assignments": 0}

    bridge = Bridge()
    bridge.calls = []
    service = colab_module.ColabService(tmp_path / "colab", bridge)
    monkeypatch.setattr(service, "start", lambda: None)
    account = service.create_account("alice@example.invalid", "Synthetic")
    service.account_action("alice@example.invalid", account["id"], "refresh", {})
    submits = []
    actual_submit = service.submit

    def submit(owner, body):
        submits.append((owner, copy.deepcopy(body)))
        return actual_submit(owner, body)

    monkeypatch.setattr(service, "submit", submit)
    user = {"email": "alice@example.invalid", "role": "member"}
    character = {"id": "owned-character", "name": "Synthetic", "visualClass": "anime",
                 "ownerEmail": user["email"], "type": "experimental", "kind": "custom"}
    catalog = {row["id"]: row for row in qwen.feature_definitions()}
    if hasattr(qwen, "character_feature_definition"):
        catalog["character-photoshoot"] = qwen.character_feature_definition()
    stages, permissions = [], []

    def character_entry(cid):
        permissions.append(cid)
        if cid != character["id"]:
            raise KeyError(cid)
        return copy.deepcopy(character)

    def save_uploaded(file_info, slot):
        stages.append(slot)
        raw = base64.b64decode(file_info["dataUrl"].split(",", 1)[1], validate=True)
        name = "owned-upload-" + slot + ".png"
        (input_root / name).write_bytes(raw)
        return name

    def capability(entry, action, feature, variant):
        if action != "photoshoot" or feature != "character-photoshoot" or variant != "photoshoot.anime.character-photoshoot.v1":
            raise ValueError("wrong character capability")
        return {"featureId": feature, "variantId": variant, "allowedOptions": {"visualStyle": ["anime"]}}

    def runtime_policy(entry, cap, runtime):
        if entry.get("releaseReady") is False:
            raise ValueError("character release policy blocks generation")
        return cap

    namespace = {"Path": Path, "os": os, "re": re, "secrets": secrets, "shutil": shutil,
        "base64": base64, "mimetypes": mimetypes, "time": time, "hmac": hmac,
        "MAX_UPLOAD_DECODED": 64 * 1024 * 1024, "RequestTooLarge": ValueError,
        "JOB_INDEX_PATH": tmp_path / "library" / ".job_index.json",
        "COMFY_INPUT": input_root, "FEATURE_BY_ID": catalog,
        "BUILDERS": {row["builder"]: lambda *args: pytest.fail("local builder called for Colab") for row in catalog.values()},
        "colab_service": colab_module, "get_colab_service": lambda: service,
        "get_character_entry": character_entry, "normalize_email": lambda value: value.strip().lower(),
        "normalized_visual_class": lambda value: str(value or "").lower(),
        "is_experimental_character": lambda entry: entry.get("type") == "experimental",
        "is_operator_user": lambda who: bool(who and who.get("role") == "admin"),
        "character_generation_context": lambda entry: copy.deepcopy(entry),
        "resolve_character_capability": capability,
        "require_character_capability_runtime": runtime_policy,
        "breezy_persona_runtime": lambda: pytest.fail("unexpected voice runtime"),
        "apply_character_capability_settings": lambda entry, cap, settings: {**settings, "visualStyle": entry["visualClass"]},
        "character_asset_path": lambda entry, key: avatar,
        "sha256_file": lambda path: hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "apply_character_visual_class_hints": lambda settings, context: settings,
        "resolve_generation_presets": lambda feature, settings: settings,
        "enforce_p0_generation_options": lambda feature, settings: settings,
        "validate_voice_generation_contract": lambda feature, settings, *args: settings,
        "save_uploaded_file": save_uploaded,
        "feature_status": lambda: pytest.fail("Colab must not require local nodes/models"),
        "CLIPMODE_CHARACTER_PHOTOSHOOT_FEATURE_ID": "clipmode-character-photoshoot",
        "CLIPMODE_CHARACTER_PHOTOSHOOT_ACTION_ID": "photoshoot",
        "CLIPMODE_CHARACTER_PHOTOSHOOT_VARIANT_ID": "clipmode-unused",
        "user_can_access_view_item": lambda who, filename, *args: filename.startswith("owned-upload-"),
    }
    load_server_functions(namespace)

    class Handler:
        _generate_result = namespace["_generate_result"]
        _compute_dispatch_started = False

        def current_user(self):
            return user

        def is_orchestrator_service_authenticated(self):
            return False

    handler = Handler()
    server = types.SimpleNamespace(**namespace)
    server.feature_status = lambda: [{"id": row["id"], "ready": False} for row in catalog.values()]
    server.request_json = lambda path: {"queue_running": [], "queue_pending": []}
    web = web_module.ComputeWeb(handler, server)
    limits = {"account_id": account["id"], "gpu": "L4", "max_units": 20, "max_minutes": 5}
    text_request = {"featureId": "qwen21-text2img", "settings": {"positive": "Synthetic blue square",
        "aspectRatio": "1:1", "megapixels": "1", "steps": 25, "cfg": 1, "negative": "", "seed": 4}, "files": {}}
    return types.SimpleNamespace(web=web, handler=handler, server=server, globals=namespace,
        service=service, bridge=bridge, account=account, user=user, character=character,
        avatar=avatar, input_root=input_root, media=media, limits=limits, request=text_request,
        submits=submits, stages=stages, permissions=permissions)


def request_id():
    return str(uuid.uuid4())


def character_request():
    return {"featureId": "character-photoshoot", "characterId": "owned-character", "action": "photoshoot",
            "variantId": "photoshoot.anime.character-photoshoot.v1",
            "settings": {"instruction": "A seaside scene", "resolution": "1024", "steps": 25,
                         "cfg": 1, "negative": "", "visualStyle": "anime"}, "files": {}}


def test_fresh_balance_rejects_stale_affordable_snapshot_without_allocation(web_fixture):
    f = web_fixture
    assert f.web.inspect_colab(f.web.owner, f.request, f.limits)["eligible"]
    f.bridge.balance = 0
    with pytest.raises(Exception) as error:
        f.web.submit_colab(f.web.owner, request_id(), f.request, f.limits)
    assert getattr(error.value, "code", "") == "colab_admission_rejected"
    assert f.bridge.calls.count("account_status") >= 2
    assert "runtime_allocate" not in f.bridge.calls
    assert f.service.snapshot(f.web.owner)["jobs"] == []


def test_denied_character_checked_before_staging_or_admission(web_fixture):
    f = web_fixture
    f.character["ownerEmail"] = "someone-else@example.invalid"
    with pytest.raises(Exception) as error:
        f.web.submit_colab(f.web.owner, request_id(), character_request(), f.limits)
    assert getattr(error.value, "code", "") == "input_validation_failed"
    assert f.permissions == ["owned-character"]
    assert f.stages == [] and f.submits == []
    assert list(f.input_root.iterdir()) == []


def test_owned_character_uses_server_avatar_and_does_not_forward_private_metadata(web_fixture):
    f = web_fixture
    body = character_request()
    body["submittedBy"] = {"email": "forged@example.invalid"}
    body["settings"].update({"_characterId": "forged", "_characterVisualClass": "realistic", "prefix": "private/path"})
    result = f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert result["state"] == "queued"
    owner, forwarded = f.submits[0]
    assert owner == f.web.owner
    assert base64.b64decode(forwarded["files"][0]["data"]) == f.media
    text = json.dumps(forwarded, ensure_ascii=False)
    assert "_character" not in text and "forged@example.invalid" not in text
    assert str(f.avatar) not in text and "private/path" not in text
    assert "全畫面採用一致的動漫畫風" in forwarded["job"]["prompt"]
    assert list(f.input_root.iterdir())
    assert "runtime_allocate" not in f.bridge.calls


def test_unknown_slot_and_nonempty_negative_do_not_submit(web_fixture):
    f = web_fixture
    for request in ({**f.request, "files": {"unlisted": {"dataUrl": "bad"}}},
                    {**f.request, "settings": {**f.request["settings"], "negative": "custom instruction"}}):
        with pytest.raises(Exception):
            f.web.submit_colab(f.web.owner, request_id(), request, f.limits)
    assert f.submits == [] and f.stages == []
    assert "runtime_allocate" not in f.bridge.calls


def test_owned_upload_uses_configured_comfy_input_root(web_fixture):
    f = web_fixture
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Make the square red", "resolution": "1024"},
            "files": {"image": {"name": "source.png", "dataUrl": "data:image/png;base64," + base64.b64encode(f.media).decode()}}}
    result = f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert result["state"] == "queued"
    staged = list(f.input_root.glob("castle_colab_*.png"))
    assert len(staged) == 1 and staged[0].read_bytes() == f.media
    assert staged[0].stat().st_mode & 0o777 == 0o600
    assert f.stages == []  # The local Comfy upload boundary was not called.
    assert base64.b64decode(f.submits[0][1]["files"][0]["data"]) == f.media


def test_owned_status_and_artifacts_never_cross_owner(web_fixture):
    f = web_fixture
    result = f.web.submit_colab(f.web.owner, request_id(), f.request, f.limits)
    job_id = result["downstream_id"]
    artifact = {"name": "result.png", "url": f"/api/colab/jobs/{job_id}/files/result.png", "sha256": "a" * 64, "size": 10}
    f.service.update(job_id, phase="completed", released=True, artifacts=[artifact])
    receipt = f.web.status_colab(f.web.owner, job_id)
    assert receipt["state"] == "completed" and receipt["artifacts"] == [artifact]
    assert f.web.recover_colab(f.web.owner, job_id)["downstream_id"] == job_id
    with pytest.raises(ValueError):
        f.web.status_colab("other@example.invalid", job_id)
    assert f.web.recover_colab("other@example.invalid", job_id) is None


def test_ambiguous_submit_preserves_original_identity_and_never_retries(web_fixture, monkeypatch):
    f = web_fixture
    def interrupted(owner, body):
        raise TimeoutError("synthetic lost response")
    monkeypatch.setattr(f.service, "submit", interrupted)
    ident = request_id()
    first = f.web.router.submit(f.web.owner, ident, f.request, "colab", f.limits)
    assert first["state"] == "submission_uncertain"
    assert f.web.router.submit(f.web.owner, ident, f.request, "colab", f.limits)["state"] == "submission_uncertain"


def test_client_internal_asset_path_is_rejected_before_staging(web_fixture):
    f = web_fixture
    foreign = f.avatar.parent / "another-users-photo.png"
    foreign.write_bytes(f.media)
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Modify image"}, "files": {"image": {
        "_characterAssetPath": str(foreign), "_characterAssetHash": hashlib.sha256(f.media).hexdigest()}}}
    with pytest.raises(Exception):
        f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert f.submits == [] and list(f.input_root.iterdir()) == []


def test_unowned_server_file_is_not_forwarded_to_cloud(web_fixture):
    f = web_fixture
    (f.input_root / "other-user.png").write_bytes(f.media)
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Modify image"},
            "files": {"image": {"serverFile": "other-user.png"}}}
    with pytest.raises(Exception):
        f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert f.submits == []


def test_owned_server_file_and_traversal_are_checked_against_input_root(web_fixture):
    f = web_fixture
    (f.input_root / "owned-upload-existing.png").write_bytes(f.media)
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Modify image"},
            "files": {"image": {"serverFile": "../owned-avatar.png"}}}
    with pytest.raises(Exception):
        f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert f.submits == []
    body["files"]["image"]["serverFile"] = "owned-upload-existing.png"
    result = f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert result["state"] == "queued" and f.stages == []


def test_incompatible_creative_option_rejected_before_any_upload_staging(web_fixture):
    f = web_fixture
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Modify image", "negative": "custom restriction"},
            "files": {"image": {"name": "source.png", "dataUrl": "data:image/png;base64," + base64.b64encode(f.media).decode()}}}
    with pytest.raises(Exception) as error:
        f.web.submit_colab(f.web.owner, request_id(), body, f.limits)
    assert getattr(error.value, "code", "") == "negative_unsupported"
    assert f.submits == [] and f.stages == [] and list(f.input_root.iterdir()) == []


def test_character_plan_checks_ownership_and_returns_only_settings_and_slot_names(web_fixture):
    f = web_fixture
    plan = f.server.plan_colab_generation_request(character_request(), f.user, False)
    assert set(plan) == {"feature_id", "settings", "file_slots"}
    assert plan["file_slots"] == ["image"]
    assert plan["settings"]["_characterVisualClass"] == "anime"
    assert str(f.avatar) not in json.dumps(plan)
    assert "_characterReference" not in plan["settings"] and "_characterId" not in plan["settings"]
    f.character["ownerEmail"] = "other@example.invalid"
    with pytest.raises(PermissionError):
        f.server.plan_colab_generation_request(character_request(), f.user, False)
    assert f.submits == [] and list(f.input_root.iterdir()) == []


def test_character_preview_uses_owned_bound_slot_without_staging(web_fixture):
    f = web_fixture
    result = f.web.inspect_colab(f.web.owner, character_request(), f.limits)
    assert result["eligible"], result
    assert f.submits == [] and f.stages == [] and list(f.input_root.iterdir()) == []


def test_member_upload_receipt_is_owned_content_bound_and_private(web_fixture):
    f = web_fixture
    name = "fresh-member-input.png"
    path = f.input_root / name
    path.write_bytes(f.media)
    assert not f.server.user_can_use_uploaded_input(f.user, name)
    f.server.record_input_upload_owner(name, f.user)
    assert f.server.user_can_use_uploaded_input(f.user, name)
    assert not f.server.user_can_use_uploaded_input({"email": "other@example.invalid"}, name)
    database = f.globals["JOB_INDEX_PATH"].parent / ".input-ownership/receipts.sqlite3"
    assert database.stat().st_mode & 0o777 == 0o600
    body = {"featureId": "qwen21-edit", "settings": {"instruction": "Modify image"},
            "files": {"image": {"serverFile": name}}}
    assert f.web.submit_colab(f.web.owner, request_id(), body, f.limits)["state"] == "queued"
    path.write_bytes(b"changed by another upload")
    assert not f.server.user_can_use_uploaded_input(f.user, name)


def test_character_release_policy_applies_to_preview_and_colab_submit(web_fixture):
    f = web_fixture
    f.character["releaseReady"] = False
    assert not f.web.inspect_colab(f.web.owner, character_request(), f.limits)["eligible"]
    with pytest.raises(Exception):
        f.web.submit_colab(f.web.owner, request_id(), character_request(), f.limits)
    assert f.submits == [] and f.stages == [] and list(f.input_root.iterdir()) == []


@pytest.mark.parametrize("test_server", ["colab_transport"], indirect=True)
def test_raw_image_colab_http_never_needs_comfy_upload_endpoint(test_server):
    """The fixture Comfy server rejects /upload/image with HTTP 405."""
    import urllib.request
    from PIL import Image
    base, opener, cookie = test_server

    def call(path, body):
        request = urllib.request.Request(base + path, data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json", "Origin": base, "Cookie": cookie})
        with opener.open(request, timeout=15) as response:
            return json.load(response)

    account = call("/api/colab/accounts", {"label": "Synthetic raw upload"})
    flow = call(f"/api/colab/accounts/{account['id']}/login", {})
    call(f"/api/colab/accounts/{account['id']}/finish", {"flow_id": flow["flow_id"], "code": "synthetic-code"})
    call(f"/api/colab/accounts/{account['id']}/refresh", {})
    image = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(image, "PNG")
    ident = request_id()
    body = {"request_id": ident, "placement": "colab", "limits": {"account_id": account["id"], "max_units": 20, "max_minutes": 5},
            "request": {"featureId": "qwen21-edit", "settings": {"instruction": "Make the square blue", "resolution": "1024"},
                        "files": {"image": {"name": "synthetic.png", "dataUrl": "data:image/png;base64," + base64.b64encode(image.getvalue()).decode()}}}}
    result = call("/api/compute/jobs", body)
    assert result["lane"] == "colab" and result["downstream_id"] == ident
    assert result["state"] in {"queued", "running"}
    call(f"/api/compute/jobs/{ident}/cancel", {})
