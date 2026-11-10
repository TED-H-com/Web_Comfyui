import json
from dataclasses import replace
from pathlib import Path
import sys
import uuid
from types import SimpleNamespace

import pytest
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_autoip_media import build_autoip_media_plan, ingest_owned_image, inspect_autoip_media, prepare_autoip_media
from compute_autoip import normalise_autoip_video_intent
from compute_routing import ComputeValidationError
from provider_fabric.autoip import AutoIPOperation
from provider_fabric.autoip_worker import _assign_fields, _prompt
from provider_fabric.models import CostQuote, MediaAsset
from provider_fabric.autoip_profiles import candidate_autoip_profiles
from provider_fabric.owned_media_source import resolve_owned_media_source
from provider_fabric.service import ActorContext, ProviderFabricService, _parse_plan_request
from provider_fabric.store import ProviderFabricStore


@pytest.fixture
def context(tmp_path):
    source_root = tmp_path / 'authorized-uploads'
    source_root.mkdir()
    image = source_root / 'synthetic.png'
    Image.new('RGB', (8, 8), (60, 90, 130)).save(image)
    store = ProviderFabricStore(tmp_path / 'fabric.sqlite3')
    store.register_system_local_provider_account('local-main', 'Local', enabled=True)
    return store, source_root, image, tmp_path / 'compute-inputs'


def ingest(context, *, owner='owner', path=None, **kwargs):
    store, source_root, image, staged = context
    return ingest_owned_image(store, owner, 'local-main', path or image,
                              source_identity='upload:synthetic', visual_class='realistic',
                              allowed_source_roots=(source_root,), staging_root=staged,
                              authorize_source=kwargs.pop('authorize_source', lambda who, candidate: who == owner and candidate == (path or image)),
                              **kwargs)


def test_ingestion_freezes_bytes_as_owned_version_and_reuses_exact_copy(context):
    store, source_root, image, staged = context
    original = image.read_bytes()
    first = ingest(context)
    second = ingest(context)
    assert first == second
    asset = store.get_owned_remote_asset('owner', first['projection_id'])
    assert len(asset['versions']) == 1 and asset['character_ids'] == []
    assert asset['metadata']['source'] == 'compute_upload'
    assert '/tmp/' not in json.dumps(first) and str(staged) not in json.dumps(first)
    canonical = MediaAsset(**first['asset'])
    frozen = resolve_owned_media_source(store, None, 'owner', canonical, allowed_file_roots=(staged,))
    assert frozen != image and frozen.read_bytes() == original and frozen.stat().st_nlink == 1
    Image.new('RGB', (8, 8), (0, 0, 0)).save(image)
    assert resolve_owned_media_source(store, None, 'owner', canonical, allowed_file_roots=(staged,)).read_bytes() == original


def test_different_owner_cannot_resolve_or_read_another_owners_binding(context):
    store, _, _, staged = context
    first = ingest(context)
    with pytest.raises(KeyError):
        store.get_owned_remote_asset('other', first['projection_id'])
    with pytest.raises(KeyError):
        resolve_owned_media_source(store, None, 'other', MediaAsset(**first['asset']), allowed_file_roots=(staged,))
    second = ingest(context, owner='other')
    assert first['projection_id'] != second['projection_id']


def test_authorization_is_required_before_any_source_file_read(context):
    with pytest.raises(ComputeValidationError) as error:
        ingest(context, path=context[1] / 'does-not-exist.png', authorize_source=lambda *args: False)
    assert error.value.code == 'source_not_owned'
    assert not context[3].exists()


@pytest.mark.parametrize('attack', ['symlink', 'hardlink', 'outside-root', 'corrupt', 'wrong-format', 'over-limit'])
def test_invalid_or_unbound_source_never_gets_a_canonical_asset(context, attack, tmp_path):
    _, root, original, staged = context
    target = root / 'invalid.png'
    kwargs = {}
    if attack == 'symlink':
        target.symlink_to(original)
    elif attack == 'hardlink':
        target.hardlink_to(original)
    elif attack == 'outside-root':
        target = tmp_path / 'outside.png'
        target.write_bytes(original.read_bytes())
    elif attack == 'corrupt':
        target.write_bytes(b'not an image')
    elif attack == 'wrong-format':
        target = root / 'wrong.jpg'
        target.write_bytes(original.read_bytes())
    else:
        target = original
        kwargs['max_bytes'] = 1
    with pytest.raises(ComputeValidationError):
        ingest(context, path=target, **kwargs)
    assert not list(staged.rglob('*.png'))


def test_character_input_cannot_be_anonymized(context):
    with pytest.raises(ComputeValidationError) as error:
        ingest(context, character_ids=['real-character'])
    assert error.value.code == 'canonical_binding_required'
    assert not context[3].exists()


def test_worker_must_explicitly_allow_staging_and_rechecks_tampering(context):
    store, _, _, staged = context
    asset = MediaAsset(**ingest(context)['asset'])
    with pytest.raises(ValueError):
        resolve_owned_media_source(store, None, 'owner', asset)
    path = resolve_owned_media_source(store, None, 'owner', asset, allowed_file_roots=(staged,))
    path.write_bytes(b'tampered')
    with pytest.raises(ValueError):
        resolve_owned_media_source(store, None, 'owner', asset, allowed_file_roots=(staged,))


@pytest.mark.parametrize('feature,settings,slots,expected_roles', [
    ('img2img', {'instruction': 'Make the cup blue.', 'steps': 20, 'guidance': 2.5, 'denoise': 1.0}, ['image'], ['image']),
    ('img2video', {'positive': 'The cup rotates.', 'seconds': 5, 'resolution': '480p', 'aspect': '16:9'}, ['image'], ['first_frame']),
    ('first-last-frame', {'positive': 'Transition between the supplied frames.', 'seconds': 5}, ['start', 'end'], ['first_frame', 'last_frame']),
])
def test_media_plan_passes_real_fabric_parser_and_worker_slot_assignment(context, feature, settings, slots, expected_roles):
    asset = ingest(context)
    plan = build_autoip_media_plan(feature, settings, {slot: asset for slot in slots},
                                  request_id=str(uuid.uuid4()), visual_class='realistic')
    parsed = _parse_plan_request(plan)
    service = object.__new__(ProviderFabricService)
    assert service._materialize_character_capability(ActorContext('owner', 'operator'), parsed) is parsed
    assert list(_assign_fields(AutoIPOperation(plan['capability']), parsed.intent.assets)) == expected_roles
    assert _prompt(parsed.creative_instruction, AutoIPOperation(plan['capability']))
    assert parsed.intent.characters == ()


def test_media_plan_refuses_extra_inputs_character_links_and_incompatible_class(context):
    asset = ingest(context)
    for slots, extra in [({'last_frame': asset}, {}), ({'image': asset, 'unknown': asset}, {}),
                         ({'image': asset}, {'character_id': 'character'}),
                         ({'image': {**asset['asset'], 'character_ids': ['character']}}, {})]:
        with pytest.raises(ComputeValidationError):
            build_autoip_media_plan('img2video', {'positive': 'A cup.'}, slots,
                                    request_id=str(uuid.uuid4()), visual_class='realistic', **extra)


def preview_runtime(*, price=10, ready=True, quote_error=None):
    account = {'provider_id': 'autoip', 'account_id': 'autoip-owned', 'enabled': True}
    calls = []
    def get_account(owner, account_id):
        assert (owner, account_id) == ('owner', 'autoip-owned')
        return account
    profiles = tuple(replace(profile, runtime_ready=ready,
                     technical_evidence_id='autoip-probe:' + 'a' * 32 if ready else None)
                     for profile in candidate_autoip_profiles())
    def quote(actor, selected, intent, decision, options):
        # Preview has no fabricated asset/version and never invokes plan/submit.
        assert intent.assets == () and intent.characters == ()
        assert decision.selected_provider_id == selected['provider_id'] == 'autoip'
        calls.append((intent, options))
        if quote_error:
            raise RuntimeError(quote_error)
        return CostQuote('autoip', intent.capability, price, remaining_credits=None, authoritative=True,
                         account_id=selected['account_id'], quote_id='fixture-price')
    runtime = SimpleNamespace(
        inventory=lambda actor: {'providers': [{**account, 'compatibility_receipt_current': True}]},
        store=SimpleNamespace(get_owned_provider_account=get_account),
        autoip_profiles_for_account=lambda owner, account_id: profiles,
        autoip_quote_source=quote)
    return runtime, calls


def test_preview_requires_current_exact_account_profile_and_fresh_quote_without_media_reads(tmp_path):
    runtime, calls = preview_runtime()
    actor = ActorContext('owner', 'operator')
    before = list(tmp_path.iterdir())
    result = inspect_autoip_media(runtime, actor, 'img2img', {'instruction': 'A blue cup.', 'steps': 20},
                                 ['image'], visual_class='realistic', max_credits=10)
    assert result['eligible'] and result['estimated_cost'] == 10 and result['warnings']
    assert len(calls) == 1 and list(tmp_path.iterdir()) == before
    assert calls[0][0].capability.value == 'image_to_image'


@pytest.mark.parametrize('change,reason', [
    ({'price': 11}, 'budget_exceeded'), ({'ready': False}, 'autoip_operation_unavailable'),
    ({'quote_error': 'private-provider-error-do-not-expose'}, 'inspection_unavailable'),
])
def test_preview_failures_never_claim_execution_readiness_or_leak_provider_error(change, reason):
    runtime, _ = preview_runtime(**change)
    result = inspect_autoip_media(runtime, ActorContext('owner', 'operator'), 'img2img',
                                 {'instruction': 'A blue cup.'}, ['image'], visual_class='realistic', max_credits=10)
    assert result['eligible'] is False and result['reasons'] == [reason]
    assert 'private-provider-error' not in json.dumps(result)


def test_prepare_checks_all_metadata_before_reading_and_binds_actual_owned_source(context):
    store, root, source, staged = context
    runtime = SimpleNamespace(store=store, config=SimpleNamespace(local_account_id='local-main'))
    actor = ActorContext('owner', 'operator')
    authorize = lambda owner, path: owner == actor.user_id and path == source
    uid = str(uuid.uuid4())
    kwargs = dict(request_id=uid, visual_class='realistic', allowed_source_roots=(root,),
                  staging_root=staged, authorize_source=authorize, max_credits=12)
    with pytest.raises(ComputeValidationError):
        prepare_autoip_media(runtime, actor, 'img2img', {'instruction': 'A blue cup.', 'steps': 999},
                             {'image': source}, **kwargs)
    assert not staged.exists()
    plan = prepare_autoip_media(runtime, actor, 'img2img', {'instruction': 'A blue cup.'},
                                {'image': source}, **kwargs)
    parsed = _parse_plan_request(plan)
    assert len(parsed.intent.assets) == 1 and plan['max_credits'] == 12
    assert resolve_owned_media_source(store, None, actor.user_id, parsed.intent.assets[0],
                                     allowed_file_roots=(staged,)).is_file()
    assert str(source) not in json.dumps(plan) and str(staged) not in json.dumps(plan)


def test_h3_image_remote_override_preserves_audio_and_first_last_roles(context):
    asset = ingest(context)
    settings, warnings = normalise_autoip_video_intent('h3-image-video', {
        'integrated_multimodal_description': 'A cup slowly rotates.',
        'overall_soundscape': 'Wind softly whistles.', 'non_diegetic_music': 'No music.',
        'frames': '124', 'aspect': 'landscape', 'acceleration': 'stock', 'seed': -1,
    }, {'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'})
    plan = build_autoip_media_plan('h3-image-video', settings, {'image': asset, 'last_frame': asset},
                                   request_id=str(uuid.uuid4()), visual_class='realistic')
    parsed = _parse_plan_request(plan)
    assert list(_assign_fields(AutoIPOperation.IMAGE_TO_VIDEO, parsed.intent.assets)) == ['first_frame', 'last_frame']
    prompt = _prompt(parsed.creative_instruction, AutoIPOperation.IMAGE_TO_VIDEO)
    assert 'Wind softly whistles.' in prompt and 'No music.' in prompt and warnings
