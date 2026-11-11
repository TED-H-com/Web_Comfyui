"""Synthetic images and fake submissions; never use a provider or user media."""
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
sys.path.insert(0, str(ROOT))
from provider_fabric.autoip import AutoIPFile, _bind_job
from provider_fabric.autoip_input_geometry import AutoIPInputGeometry, FIT_POLICY, LEGACY_PROFILE_REVISION
from provider_fabric.autoip_worker import AutoIPSubmissionProcessor
from provider_fabric.models import MediaAsset, MediaKind, VisualClass
from provider_fabric.provider_worker import DefiniteLocalFailure
from compute_autoip_media import _media_plan_spec
import scripts.test_autoip_worker as worker_fixtures


def source(tmp_path, size=(200, 400), visual='anime', *, character=False):
    path = tmp_path / 'source.png'
    Image.new('RGB', size, (50, 100, 180)).save(path)
    data = path.read_bytes()
    asset = MediaAsset('synthetic-image', MediaKind.IMAGE, path.as_uri(), VisualClass(visual),
        ('synthetic-character',) if character else (), 'image/png', len(data), hashlib.sha256(data).hexdigest())
    return asset, AutoIPFile('first_frame', path, asset.visual_class, asset.mime_type)


def prepare(preparer, asset, loaded, *, owner='owner', request='request:one', **kwargs):
    return preparer(owner, request, asset, loaded,
        policy=FIT_POLICY, resolution='480p', aspect_ratio='16:9', **kwargs)


@pytest.mark.parametrize('visual', ['anime', 'realistic'])
@pytest.mark.parametrize('size', [(200, 400), (300, 300), (800, 100), (864, 480), (1728, 960)])
def test_contain_keeps_entire_source_and_same_ratio_is_byte_identity(tmp_path, visual, size):
    asset, loaded = source(tmp_path, size, visual)
    before = loaded.path.read_bytes()
    preparer = AutoIPInputGeometry(tmp_path / 'private')
    prepared = prepare(preparer, asset, loaded)
    proof = preparer.receipt('owner', 'request:one', 'first_frame')
    assert loaded.path.read_bytes() == before
    assert proof['source']['sha256'] == asset.sha256
    assert prepared.path != loaded.path and prepared.visual_class == asset.visual_class
    assert prepared.path.stat().st_mode & 0o777 == 0o600
    assert proof['upload']['sha256'] == hashlib.sha256(prepared.path.read_bytes()).hexdigest()
    assert proof['transform']['crop'] is False
    if size[0] * 480 == size[1] * 864:
        assert prepared.path.read_bytes() == before
        assert proof['transform']['operation'] == 'identity'
        assert proof['upload']['sha256'] == proof['source']['sha256']
    else:
        with Image.open(prepared.path) as image:
            assert image.size == (864, 480)
            box = image.getbbox()
            assert box is not None
            w, h = box[2] - box[0], box[3] - box[1]
            assert abs(w * size[1] - h * size[0]) <= max(size)
            assert w == 864 or h == 480
            assert tuple(proof['transform']['offset']) == box[:2]
            assert image.getpixel((box[0], box[1])) == (50, 100, 180)
        assert proof['upload']['sha256'] != proof['source']['sha256']


def test_retry_reuses_frozen_upload_and_receipt_without_a_second_resize(tmp_path, monkeypatch):
    asset, loaded = source(tmp_path)
    preparer = AutoIPInputGeometry(tmp_path / 'private')
    first = prepare(preparer, asset, loaded)
    proof = preparer.receipt('owner', 'request:one', 'first_frame')
    before = first.path.stat().st_mtime_ns
    monkeypatch.setattr(Image.Image, 'resize', lambda *a, **k: pytest.fail('retry must not resize again'))
    second = prepare(AutoIPInputGeometry(tmp_path / 'private'), asset, loaded)
    assert second.path == first.path and second.path.stat().st_mtime_ns == before
    assert preparer.receipt('owner', 'request:one', 'first_frame') == proof


@pytest.mark.parametrize('size', [(400, 800), (600, 600), (1200, 200)])
def test_all_four_source_corners_survive_contain_without_cropping(tmp_path, size):
    from PIL import ImageDraw
    asset, loaded = source(tmp_path, size)
    image = Image.open(loaded.path).convert('RGB')
    width, height = size
    draw = ImageDraw.Draw(image)
    colors = [(220, 10, 10), (10, 220, 10), (10, 10, 220), (220, 220, 10)]
    for (x, y), color in zip(((0, 0), (.85, 0), (0, .85), (.85, .85)), colors):
        draw.rectangle((round(x * width), round(y * height), round((x + .15) * width), round((y + .15) * height)), fill=color)
    image.save(loaded.path)
    asset = replace(asset, sha256=hashlib.sha256(loaded.path.read_bytes()).hexdigest(), byte_size=loaded.path.stat().st_size)
    geometry = AutoIPInputGeometry(tmp_path / 'geometry')
    prepared = prepare(geometry, asset, loaded)
    proof = geometry.receipt('owner', 'request:one', 'first_frame')
    ox, oy = proof['transform']['offset'];width, height = proof['transform']['content_size']
    with Image.open(prepared.path) as result:
        for (x, y), color in zip(((.075, .075), (.925, .075), (.075, .925), (.925, .925)), colors):
            assert result.getpixel((ox + round(width * x), oy + round(height * y))) == color


def test_concurrent_preparation_publishes_one_immutable_receipt(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    asset, loaded = source(tmp_path)
    root = tmp_path / 'geometry'
    resizes = []
    original = Image.Image.resize
    def counted(image, *args, **kwargs):
        resizes.append(1)
        return original(image, *args, **kwargs)
    monkeypatch.setattr(Image.Image, 'resize', counted)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: prepare(AutoIPInputGeometry(root), asset, loaded), range(4)))
    assert len({result.path for result in results}) == 1 and len(resizes) == 1
    assert not list(root.rglob('.prepare-*'))


@pytest.mark.parametrize('changed', ['upload', 'source', 'receipt', 'symlink'])
def test_tampering_rejects_without_reblessing_existing_preparation(tmp_path, changed):
    asset, loaded = source(tmp_path)
    preparer = AutoIPInputGeometry(tmp_path / 'private')
    prepared = prepare(preparer, asset, loaded)
    receipt = prepared.path.parent / 'first_frame.json'
    if changed == 'upload':
        prepared.path.write_bytes(b'tampered')
    elif changed == 'source':
        loaded.path.write_bytes(b'tampered')
    elif changed == 'receipt':
        data = json.loads(receipt.read_text());data['source']['sha256'] = '0' * 64
        receipt.write_text(json.dumps(data))
    else:
        prepared.path.unlink();prepared.path.symlink_to(loaded.path)
    before = receipt.read_bytes()
    with pytest.raises((ValueError, OSError)):
        prepare(preparer, asset, loaded)
    assert receipt.read_bytes() == before


def test_owner_slot_and_request_bindings_do_not_share_mutable_receipts(tmp_path):
    asset, loaded = source(tmp_path)
    preparer = AutoIPInputGeometry(tmp_path / 'private')
    first = prepare(preparer, asset, loaded)
    other = prepare(preparer, asset, loaded, owner='other')
    last = prepare(preparer, asset, replace(loaded, field_name='last_frame'))
    assert first.path.parent != other.path.parent
    assert preparer.receipt('owner', 'request:one', 'last_frame')['field_name'] == 'last_frame'
    assert last.path == first.path  # Same content, separate role-bound receipts.
    with pytest.raises(ValueError):
        prepare(preparer, replace(asset, asset_id='different-source'), loaded)
    assert preparer.receipt('owner', 'request:one', 'first_frame')['source']['asset_id'] == asset.asset_id


def test_exif_orientation_and_alpha_are_explicit_and_never_crop(tmp_path):
    path = tmp_path / 'oriented.jpg'
    exif = Image.Exif();exif[274] = 6
    Image.new('RGB', (100, 200), 'red').save(path, exif=exif)
    data = path.read_bytes()
    asset = MediaAsset('oriented', MediaKind.IMAGE, path.as_uri(), VisualClass.REALISTIC,
        (), 'image/jpeg', len(data), hashlib.sha256(data).hexdigest())
    preparer = AutoIPInputGeometry(tmp_path / 'private')
    prepared = prepare(preparer, asset, AutoIPFile('first_frame', path, asset.visual_class, asset.mime_type))
    proof = preparer.receipt('owner', 'request:one', 'first_frame')
    assert (proof['source']['width'], proof['source']['height']) == (200, 100)
    assert proof['source']['exif_orientation'] == 6 and path.read_bytes() == data
    with Image.open(prepared.path) as image:
        assert image.size == (864, 480) and image.getexif().get(274, 1) == 1
    alpha = tmp_path / 'alpha.png'
    Image.new('RGBA', (100, 100), (255, 0, 0, 0)).save(alpha)
    data = alpha.read_bytes();asset = replace(asset, asset_id='alpha', uri=alpha.as_uri(), mime_type='image/png', byte_size=len(data), sha256=hashlib.sha256(data).hexdigest())
    output = prepare(preparer, asset, AutoIPFile('first_frame', alpha, asset.visual_class, asset.mime_type), request='request:alpha')
    with Image.open(output.path) as image:
        assert image.mode == 'RGB' and image.getbbox() is None


@pytest.mark.parametrize('resolution,aspect', [('480p', '9:16'), ('768p', '16:9'), ('2k', '9:16')])
def test_unknown_targets_keep_old_plan_and_warn_instead_of_guessing(resolution, aspect, tmp_path):
    plan, _, warnings = _media_plan_spec('h3-image-video',
        {'prompt': 'A synthetic paper kite.', 'resolution': resolution, 'aspect_ratio': aspect}, ['image'],
        request_id='00000000-0000-0000-0000-000000000001', visual_class='anime')
    legacy, _, legacy_warnings = _media_plan_spec('h3-image-video',
        {'prompt': 'A synthetic paper kite.', 'resolution': resolution, 'aspect_ratio': aspect}, ['image'],
        request_id='00000000-0000-0000-0000-000000000001', visual_class='anime', fit_video_frame=False)
    assert 'frame_fit' not in plan['options'] and plan['options'] == legacy['options']
    assert plan['options']['resolution'] == resolution and plan['options']['aspect_ratio'] == aspect
    added_warnings = [warning for warning in warnings if warning not in legacy_warnings]
    assert len(added_warnings) == 1 and isinstance(added_warnings[0], str) and added_warnings[0].strip()
    asset, loaded = source(tmp_path)
    with pytest.raises(ValueError, match='canvas'):
        AutoIPInputGeometry(tmp_path / 'private')('owner', 'request:one', asset, loaded,
            policy=FIT_POLICY, resolution=resolution, aspect_ratio=aspect)
    assert not (tmp_path / 'private').exists()


@pytest.fixture
def worker_harness(tmp_path):
    fixture = worker_fixtures.AutoIPWorkerTests()
    fixture.setUp()
    asset, loaded = source(tmp_path, character=True)
    fixture.path = loaded.path
    fixture.asset = replace(asset, asset_id='castle-character:hero:avatar',
        uri='castle://character-assets/hero/avatar', character_ids=('hero',))
    fixture.intent = replace(fixture.intent, assets=(fixture.asset,))
    yield fixture
    fixture.doCleanups()


@pytest.mark.parametrize('origin', ['generic', 'bound'])
def test_common_worker_prepares_before_submit_without_changing_canonical_or_dependency_source(worker_harness, tmp_path, monkeypatch, origin):
    fixture = worker_harness
    if origin != 'bound':
        asset = replace(fixture.asset, asset_id='provider-version:assetver:synthetic',
            uri='castle://assets/synthetic', character_ids=())
        fixture.intent = replace(fixture.intent, assets=(asset,), characters=())
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=FIT_POLICY)
    original = copy.deepcopy(bundle)
    # Public constructor works without any private entrypoint injection.
    monkeypatch.setenv('CASTLE_AUTOIP_INPUT_GEOMETRY_ROOT', str(tmp_path / 'cache'))
    receipt = fixture.processor()(bundle)
    assert receipt.external_task_id == 'autoip-task-001' and len(fixture.client.calls) == 1
    intent, _, job, request_id, _ = fixture.client.calls[0]
    assert bundle == original and intent.to_dict() == original['intent']['intent']
    assert intent.assets[0].sha256 == fixture.asset.sha256
    assert 'frame_fit' not in job.fields
    bound = _bind_job(job)
    assert bound.normalized.inputs[0].sha256 != intent.assets[0].sha256
    proof = AutoIPInputGeometry(tmp_path / 'cache').receipt('member-one', request_id, 'first_frame')
    assert proof['source']['sha256'] == intent.assets[0].sha256
    assert proof['source']['character_ids'] == list(intent.assets[0].character_ids)
    assert proof['upload']['sha256'] == bound.normalized.inputs[0].sha256
    with Image.open(job.files[0].path) as image:
        assert image.size == (864, 480)


def test_pipeline_handoff_and_owned_ingestion_keep_dependency_hash_while_worker_uploads_derivative(worker_harness, tmp_path):
    from compute_execution import PipelineExecution
    from compute_autoip_media import prepare_autoip_media
    from provider_fabric.owned_media_source import resolve_owned_media_source
    from provider_fabric.service import ActorContext, _parse_plan_request
    from provider_fabric.store import ProviderFabricStore

    user = {'email': 'synthetic@example.invalid', 'role': 'operator'}
    registrations = []
    output = tmp_path / 'output';output.mkdir()
    Image.new('RGB', (100, 200), 'blue').save(output / 'result.png')
    server = SimpleNamespace(COMFY_INPUT=tmp_path / 'input', COMFY_OUTPUT=output,
        user_for_email=lambda owner: copy.deepcopy(user) if owner == user['email'] else None,
        get_colab_service=lambda: SimpleNamespace(root=tmp_path / 'colab'),
        user_can_access_view_item=lambda actor, name, subfolder, kind, task: actor['email'] == user['email'] and task == 'image-task',
        record_input_upload_owner=lambda name, actor: registrations.append((name, actor['email'])))
    pipeline = PipelineExecution(server)
    receipt = {'state': 'completed', 'downstream_id': 'image-task', 'artifacts': [
        {'name': 'result.png', 'url': '/api/view?filename=result.png&subfolder=&type=output&promptId=image-task'}]}
    dependency = pipeline.resolve_artifact(user['email'], receipt, 0)
    frozen_dependency = copy.deepcopy(dependency)
    source_path = pipeline.s.COMFY_INPUT / dependency['file']['serverFile']
    store = ProviderFabricStore(tmp_path / 'fabric.sqlite3')
    store.register_system_local_provider_account('local-main', 'Local', enabled=True)
    actor = ActorContext('member-one', 'operator')
    runtime = SimpleNamespace(store=store, config=SimpleNamespace(local_account_id='local-main'))
    staged = tmp_path / 'adopted'
    plan = prepare_autoip_media(runtime, actor, 'h3-image-video', {'prompt': 'A synthetic blue square.'},
        {'image': source_path}, request_id='00000000-0000-0000-0000-000000000001', visual_class='anime',
        allowed_source_roots=(pipeline.s.COMFY_INPUT,), staging_root=staged,
        authorize_source=lambda owner, path: owner == actor.user_id and path == source_path)
    parsed = _parse_plan_request(plan)
    fixture = worker_harness
    fixture.intent = parsed.intent
    fixture.path = resolve_owned_media_source(store, None, actor.user_id, parsed.intent.assets[0], allowed_file_roots=(staged,))
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'] = plan['options']
    bundle['intent']['payload']['creative_instruction'] = parsed.creative_instruction
    processor = fixture.processor()
    processor._input_geometry_root = tmp_path / 'geometry'
    processor(bundle)
    assert dependency == frozen_dependency
    assert parsed.intent.assets[0].sha256 == dependency['sha256']
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == dependency['sha256']
    proof = AutoIPInputGeometry(tmp_path / 'geometry').receipt(actor.user_id, 'request-001', 'first_frame')
    assert proof['source']['sha256'] == dependency['sha256']
    assert proof['upload']['sha256'] != dependency['sha256']
    assert registrations == [(source_path.name, user['email'])]


def test_default_public_cache_root_and_local_decode_failure_are_safe(worker_harness, tmp_path, monkeypatch):
    monkeypatch.delenv('CASTLE_AUTOIP_INPUT_GEOMETRY_ROOT', raising=False)
    monkeypatch.setenv('XDG_CACHE_HOME', str(tmp_path / 'xdg'))
    fixture = worker_harness
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=FIT_POLICY)
    fixture.processor()(bundle)
    assert (tmp_path / 'xdg' / 'castle-comfy-web' / 'autoip-input-geometry').is_dir()
    fixture.client.calls.clear()
    fixture.path.write_bytes(b'invalid-but-hash-bound-image')
    fixture.asset = replace(fixture.asset, sha256=hashlib.sha256(fixture.path.read_bytes()).hexdigest(),
                            byte_size=fixture.path.stat().st_size)
    fixture.intent = replace(fixture.intent, assets=(fixture.asset,))
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=FIT_POLICY)
    with pytest.raises(DefiniteLocalFailure, match='input preparation failed'):
        fixture.processor()(bundle)
    assert fixture.client.calls == []


@pytest.mark.parametrize('revision,policy', [('unknown-revision', None), (LEGACY_PROFILE_REVISION, FIT_POLICY)])
def test_legacy_binding_cannot_authorize_a_new_or_changed_policy(worker_harness, tmp_path, revision, policy):
    fixture = worker_harness
    bundle = fixture.bundle()
    bundle['intent']['payload']['capability_grant']['profile_revision'] = revision
    if policy:
        bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=policy)
    with pytest.raises(DefiniteLocalFailure, match='readiness evidence changed'):
        fixture.processor()(bundle)
    assert fixture.client.calls == []


@pytest.mark.parametrize('resolution,aspect', [('480p', '9:16'), ('768p', '16:9'), ('2k', '9:16')])
def test_unknown_targets_still_execute_original_upload_without_preparation(worker_harness, resolution, aspect):
    fixture = worker_harness
    plan, _, warnings = _media_plan_spec('h3-image-video',
        {'prompt': 'A synthetic paper kite.', 'resolution': resolution, 'aspect_ratio': aspect}, ['image'],
        request_id='00000000-0000-0000-0000-000000000001', visual_class='anime')
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'] = plan['options']
    before = fixture.path.read_bytes()
    fixture.processor()(bundle)
    assert len(fixture.client.calls) == 1
    submitted = fixture.client.calls[0][2]
    assert submitted.files[0].path == fixture.path
    assert submitted.files[0].path.read_bytes() == before
    assert hashlib.sha256(before).hexdigest() == fixture.asset.sha256
    assert 'frame_fit' not in plan['options'] and 'frame_fit' not in submitted.fields
    assert submitted.fields['resolution'] == resolution and submitted.fields['aspect_ratio'] == aspect
    assert len(warnings) == 1 and isinstance(warnings[0], str) and warnings[0].strip()


def test_first_and_last_frame_keep_order_and_each_gets_its_own_source_receipt(worker_harness, tmp_path):
    fixture = worker_harness
    last_dir = tmp_path / 'last';last_dir.mkdir()
    last, loaded_last = source(last_dir, (500, 100), character=True)
    last = replace(last, asset_id='last', uri='castle://assets/last', character_ids=('hero',))
    fixture.intent = replace(fixture.intent, assets=(fixture.asset, last))
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=FIT_POLICY)
    geometry = AutoIPInputGeometry(tmp_path / 'geometry')
    processor = AutoIPSubmissionProcessor(lambda *args: fixture.client,
        lambda owner, asset, role: AutoIPFile(role, fixture.path if asset.asset_id == fixture.asset.asset_id else loaded_last.path,
            asset.visual_class, asset.mime_type), profile_resolver=fixture.profile,
        quote_verifier=lambda *args: fixture.quote, input_geometry_preparer=geometry)
    processor(bundle)
    files = fixture.client.calls[0][2].files
    assert [file.field_name for file in files] == ['first_frame', 'last_frame']
    for role, asset in zip(('first_frame', 'last_frame'), fixture.intent.assets):
        assert geometry.receipt('member-one', 'request-001', role)['source']['sha256'] == asset.sha256


@pytest.mark.parametrize('aspect', ['16:9', '9:16'])
def test_legacy_v4_bound_job_keeps_original_bytes_and_constructor_does_not_create_cache(worker_harness, tmp_path, monkeypatch, aspect):
    fixture = worker_harness;bundle = fixture.bundle()
    bundle['intent']['payload']['options']['aspect_ratio'] = aspect
    bundle['intent']['payload']['capability_grant']['profile_revision'] = LEGACY_PROFILE_REVISION
    monkeypatch.setenv('CASTLE_AUTOIP_INPUT_GEOMETRY_ROOT', str(tmp_path / 'unused'))
    fixture.processor()(bundle)
    assert fixture.client.calls[0][2].files[0].path == fixture.path
    assert not (tmp_path / 'unused').exists()


def test_unknown_policy_and_source_mutation_never_reach_submission(worker_harness, tmp_path):
    fixture = worker_harness;bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit='guess-canvas')
    with pytest.raises(DefiniteLocalFailure):
        fixture.processor()(bundle)
    assert fixture.client.calls == []
    bundle['intent']['payload']['options']['frame_fit'] = FIT_POLICY
    fixture.path.write_bytes(b'changed')
    with pytest.raises(DefiniteLocalFailure):
        fixture.processor()(bundle)
    assert fixture.client.calls == []


@pytest.mark.parametrize('ambiguous', [False, True])
def test_real_client_posts_frozen_derivative_once_and_never_sends_internal_policy(worker_harness, tmp_path, monkeypatch, ambiguous):
    from email import policy as email_policy
    from email.parser import BytesParser
    from provider_fabric.autoip import AutoIPClient, HttpResponse
    from provider_fabric.config import ProviderConfig, SecretKind, SecretReference

    fixture = worker_harness
    geometry = AutoIPInputGeometry(tmp_path / 'geometry')
    calls = []
    def send(request):
        calls.append(request)
        assert request.method == 'POST' and request.url.endswith('/videos/image-to-video')
        proof = geometry.receipt('member-one', 'request-001', 'first_frame')
        encoded = b'Content-Type: ' + request.headers['Content-Type'].encode() + b'\r\n\r\n' + request.body
        multipart = BytesParser(policy=email_policy.default).parsebytes(encoded)
        parts = {part.get_param('name', header='content-disposition'): part for part in multipart.iter_parts()}
        assert 'frame_fit' not in parts
        actual = parts['first_frame'].get_payload(decode=True)
        assert hashlib.sha256(actual).hexdigest() == proof['upload']['sha256']
        assert proof['source']['sha256'] == fixture.asset.sha256
        assert hashlib.sha256(fixture.path.read_bytes()).hexdigest() == fixture.asset.sha256
        if ambiguous:
            raise TimeoutError('synthetic ambiguous transport')
        return HttpResponse(200, {}, json.dumps({'data': {'task_uuid': 'synthetic-task',
            'credits_charged': 7, 'remaining_credits': 93}}).encode())
    monkeypatch.setenv('SYNTHETIC_GEOMETRY_KEY', 'aip_' + 'synthetic')
    client = AutoIPClient(ProviderConfig('autoip', 'https://autoip.app/api/v1',
        SecretReference(SecretKind.ENV, 'SYNTHETIC_GEOMETRY_KEY'), enabled=True),
        transport=SimpleNamespace(send=send))
    processor = AutoIPSubmissionProcessor(lambda *args: client,
        lambda owner, asset, role: AutoIPFile(role, fixture.path, asset.visual_class, asset.mime_type),
        profile_resolver=fixture.profile, quote_verifier=lambda *args: fixture.quote, input_geometry_preparer=geometry)
    bundle = fixture.bundle()
    bundle['intent']['payload']['options'].update(aspect_ratio='16:9', frame_fit=FIT_POLICY)
    result = processor(bundle)
    assert result.state.value == ('submission_uncertain' if ambiguous else 'submitted')
    with pytest.raises(DefiniteLocalFailure):
        processor(bundle)
    assert len(calls) == 1


def test_internal_fit_policy_does_not_change_current_pricing_dimensions():
    from provider_fabric.autoip import AutoIPPricingCatalog, AutoIPPricingEntry
    from provider_fabric.autoip_quote import _matching_entry
    catalog = AutoIPPricingCatalog(entries=(AutoIPPricingEntry('image_to_video', 30,
        (('resolution', '480p'), ('duration_seconds', 5))),), authoritative=True, catalog_sha256='a' * 64)
    options = {'resolution': '480p', 'duration_seconds': 5, 'aspect_ratio': '16:9'}
    assert _matching_entry(catalog, 'image_to_video', options) == _matching_entry(catalog, 'image_to_video', {**options, 'frame_fit': FIT_POLICY})
