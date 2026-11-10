"""Real source authorization/materialization + synthetic disk avatars, no provider."""
import ast
import copy
from dataclasses import replace
import hashlib
import json
import re
import types
import urllib.parse
import uuid

import pytest

from test_compute_web import ROOT, web_fixture, character_request


@pytest.fixture(autouse=True)
def feature_catalog_layout(monkeypatch):
    """Some deployments keep the portrait definition in feature_definitions."""
    monkeypatch.syspath_prepend(str(ROOT/'webui'))
    import qwen_image21 as qwen
    if not hasattr(qwen, 'character_feature_definition'):
        monkeypatch.setattr(qwen, 'character_feature_definition',
            lambda: copy.deepcopy(next(row for row in qwen.feature_definitions() if row['id']=='character-photoshoot')), raising=False)


@pytest.fixture
def character_web(web_fixture):
    f = web_fixture
    from provider_fabric.service import ActorContext, ProviderFabricService, ProviderServiceError, _parse_plan_request
    from provider_fabric.autoip_profiles import candidate_autoip_profiles
    import minimax_h3_product as H3
    f.actor = ActorContext('owner-alice', 'member')
    f.server.FEATURE_BY_ID.update({row['id']: row for row in H3.H3_FEATURES})
    f.source_caps = []
    for style in ('anime', 'realistic'):
        for feature, action, variant in [
            ('character-photoshoot', 'photoshoot', f'photoshoot.{style}.character-photoshoot.v1'),
            ('h3-image-video', 'h3-image-video', f'image-video.{style}.h3-fl2va.v1'),
            ('h3-reference-video', 'h3-reference-video', f'reference-video.{style}.h3-ref2va-image.v1'),
        ]:
            f.source_caps.append({'featureId': feature, 'actionId': action, 'variantId': variant, 'enabled': True})
    profiles = [replace(p, runtime_ready=True, technical_evidence_id='autoip-probe:' + 'a'*32)
                for p in candidate_autoip_profiles()]
    runtime = types.SimpleNamespace(autoip_profiles=lambda actor: profiles)
    f.rights = types.SimpleNamespace(revoked=False, allowed_provider_ids={'autoip'},
        allowed_capabilities={'image_to_image', 'image_to_video', 'multi_subject_video'}, allowed_usage_scopes={'private'})
    f.globals.update(_PROVIDER_CANONICAL_CONTEXT=types.SimpleNamespace(actor=f.actor, user=f.user, runtime=runtime),
        character_capabilities=lambda entry: f.source_caps, legacy_character_capabilities=lambda entry: [],
        legacy_character_image_capabilities=lambda entry: [],
        content_sha256_cached=lambda p: hashlib.sha256(p.read_bytes()).hexdigest(),
        urllib=urllib, character_rights_store=lambda: types.SimpleNamespace(get=lambda *args: f.rights))
    # Private deployments apply owner profile prose in the shared read-only
    # planner. Bind that lookup to a synthetic, absent profile DB only.
    f.globals['character_profiles'] = types.SimpleNamespace(ROOT=f.avatar.parent/'absent-profiles',
        library_character_id=lambda cid: cid,
        character_generation_profile=lambda cid: {'subject':'woman','appearance':'synthetic appearance'},
        validate_gender=lambda value: None, apply_prompt_subject=lambda settings,feature: settings)
    tree = ast.parse((ROOT/'webui/server.py').read_text())
    names = {'resolve_character_capability', 'provider_fabric_canonical_resource_resolver', 'provider_fabric_capability_materializer'}
    nodes = [ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)]
    nodes += [copy.deepcopy(node) for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes, type_ignores=[])), '<authorized server functions>', 'exec'), f.globals)
    for name in names:
        setattr(f.server, name, f.globals[name])
    service = object.__new__(ProviderFabricService)
    service._capability_materializer = f.globals['provider_fabric_capability_materializer']
    f.plans, f.accepted, f.cost = [], [], 30

    def plan(actor, body):
        parsed = _parse_plan_request(body)
        chars, assets = f.server.provider_fabric_canonical_resource_resolver(actor,
            tuple(c.character_id for c in parsed.intent.characters), tuple(a.asset_id for a in parsed.intent.assets))
        parsed = replace(parsed, intent=replace(parsed.intent,
            characters=tuple(chars[c.character_id] for c in parsed.intent.characters),
            assets=tuple(assets[a.asset_id] for a in parsed.intent.assets)))
        parsed = service._materialize_character_capability(actor, parsed)
        f.plans.append(parsed)
        return {'intent': parsed.intent.to_dict(), 'quote': {'credits': f.cost}}

    def submit(actor, body, *, expected_resources=None):
        planned = plan(actor, body['plan'])
        assert expected_resources == {key: body['plan'][key] for key in ('characters','assets')}
        if any(planned['intent'][key] != expected_resources[key] for key in expected_resources):
            raise ProviderServiceError('source_changed', 'test source changed')
        if f.cost > body['plan']['max_credits']:
            raise ProviderServiceError('spend_limit_exceeded', 'test quote changed')
        f.accepted.append(copy.deepcopy(body))
        return {'intent_id': 'intent:synthetic', 'status_path': '/api/intents/intent:synthetic/status'}

    runtime.service = types.SimpleNamespace(plan_intent=plan, submit_generation=submit)
    f.runtime = runtime
    f.handler._provider_runtime_actor = lambda: (runtime, f.actor)
    return f


def request(style='anime', feature='character-photoshoot'):
    result = character_request()
    result['variantId'] = f'photoshoot.{style}.character-photoshoot.v1'
    result['settings']['visualStyle'] = style
    if feature != 'character-photoshoot':
        variant = f'image-video.{style}.h3-fl2va.v1' if feature == 'h3-image-video' else f'reference-video.{style}.h3-ref2va-image.v1'
        result.update(featureId=feature, action=feature, variantId=variant,
            computeAutoipVideo={'duration_seconds': 5, 'resolution': '480p', 'aspect_ratio': '16:9'},
            settings={'description': 'The adult smiles beside a blue wall.', 'motionProfile': 'gentle' if style == 'anime' else 'natural',
                      'aspect': 'landscape', 'frames': '124', 'acceleration': 'stock', 'seed': -1})
    return result


@pytest.mark.parametrize('style', ['anime', 'realistic'])
@pytest.mark.parametrize('feature,operation', [('character-photoshoot','image_to_image'),
    ('h3-image-video','image_to_video'), ('h3-reference-video','multi_subject_video')])
def test_actual_form_retains_original_binding_and_materialized_provider_grant(character_web, style, feature, operation):
    f = character_web
    f.character['visualClass'] = style
    body = request(style, feature)
    before = copy.deepcopy(body)
    inspected = f.web.inspect_autoip(f.web.owner, body, {'max_credits': 40})
    assert inspected['eligible'], inspected
    assert inspected['estimated_cost'] == 30 and inspected['warnings']
    rid = str(uuid.uuid4())
    compiled = f.web._autoip_character_plan(f.runtime, f.actor, body, {'max_credits':40}, rid)
    assert compiled['origin']['variant_id'] == body['variantId']
    assert compiled['origin']['action_id'] == body['action']
    receipt = f.web.submit_autoip(f.web.owner, rid, body, {'max_credits':40})
    assert receipt['downstream_id'] == 'intent:synthetic'
    assert body == before and len(f.accepted) == 1
    plan = f.accepted[0]['plan']
    assert ('frame_fit' in plan['options']) == (operation == 'image_to_video')
    assert any('補黑邊' in warning for warning in inspected['warnings']) == (operation == 'image_to_video')
    assert plan['action_id'] == operation.replace('_','-')
    assert plan['feature_id'] == 'provider-' + operation.replace('_','-')
    assert plan['characters'][0]['character_id'] == f.character['id']
    assert plan['assets'][0]['character_ids'] == [f.character['id']]
    assert plan['assets'][0]['sha256'] == hashlib.sha256(f.media).hexdigest()
    assert f.plans[-1].materialized.authorized_character_ids == (f.character['id'],)
    text = plan['creative_instruction']['frame_intent']
    assert ('全畫面採用一致的' in text if feature == 'character-photoshoot' else 'Motion:' in text)
    from provider_fabric.autoip_worker import _assign_fields, _prompt
    from provider_fabric.autoip import AutoIPOperation
    expected_role = {'image_to_image':'image','image_to_video':'first_frame','multi_subject_video':'images[]'}[operation]
    assert _assign_fields(AutoIPOperation(operation), f.plans[-1].intent.assets) == (expected_role,)
    assert _prompt(f.plans[-1].creative_instruction, AutoIPOperation(operation))


@pytest.mark.parametrize('style', ['anime', 'realistic'])
@pytest.mark.parametrize('feature', ['character-photoshoot', 'h3-image-video', 'h3-reference-video'])
def test_real_ui_decoration_and_preview_marker_recompute_canonical_avatar(character_web, style, feature):
    f = character_web
    f.character['visualClass'] = style
    body = request(style, feature)
    body['settings'].update(_characterId=f.character['id'], _characterName=f.character['name'])
    slot = 'reference' if feature == 'h3-reference-video' else 'image'
    body['files'] = {slot: {'name': slot}}
    before = copy.deepcopy(body)
    inspected = f.web.inspect_autoip(f.web.owner, body, {'max_credits': 40})
    assert inspected['eligible'], inspected
    assert body == before
    assert f.plans[-1].intent.assets[0].sha256 == hashlib.sha256(f.media).hexdigest()
    # A preview marker cannot be replayed as an actual replacement upload.
    with pytest.raises(Exception) as error:
        f.web.submit_autoip(f.web.owner, str(uuid.uuid4()), body, {'max_credits': 40})
    assert error.value.code == 'input_slots_unsupported' and not f.accepted
    body['files'] = {}
    f.web.submit_autoip(f.web.owner, str(uuid.uuid4()), body, {'max_credits': 40})
    assert len(f.accepted) == 1 and not f.stages
    assert f.accepted[0]['plan']['characters'][0]['character_id'] == f.character['id']


@pytest.mark.parametrize('settings', [
    {'_characterName': 'Other character'}, {'_characterId': 'foreign'},
    {'_characterName': 123}, {'_characterVisualClass': 'realistic'},
    {'_characterReference': '/private/untrusted.png'}, {'_characterActionId': 'photoshoot'},
])
def test_ui_decoration_never_grants_or_overrides_character_context(character_web, settings):
    f = character_web
    body = request()
    body['settings'].update(settings)
    assert not f.web.inspect_autoip(f.web.owner, body, {'max_credits': 40})['eligible']
    assert not f.plans and not f.accepted


@pytest.mark.parametrize('files', [
    {'image': {'name': 'replacement.png'}}, {'image': {'name': 'image', 'size': 12}},
    {'image': {'name': 'image', 'serverFile': 'image'}},
    {'image': {'name': 'image', 'dataUrl': 'data:image/png;base64,AA=='}},
    {'image': {'name': 'image'}, 'image2': {'name': 'image2'}}, {'reference': {'name': 'reference'}},
])
def test_preview_rejects_noncanonical_markers_and_extra_inputs(character_web, files):
    f = character_web
    body = request()
    body['files'] = files
    assert f.web.inspect_autoip(f.web.owner, body, {'max_credits': 40})['reasons'] == ['input_slots_unsupported']
    assert not f.plans and not f.accepted


@pytest.mark.parametrize('attack', ['foreign-owner','wrong-action','wrong-variant','forged-internal','replacement',
                                  'disabled','style-conflict','raw-style-conflict','extra-input','unsupported-feature'])
def test_invalid_character_never_reaches_provider_planning(character_web, attack):
    f = character_web
    body = request()
    if attack == 'foreign-owner': f.character['ownerEmail'] = 'foreign@example.invalid'
    elif attack == 'wrong-action': body['action'] = 'dance'
    elif attack == 'wrong-variant': body['variantId'] = 'photoshoot.realistic.character-photoshoot.v1'
    elif attack == 'forged-internal': body['settings']['_characterId'] = 'foreign'
    elif attack == 'replacement': body['files'] = {'image': {'serverFile':'foreign.png'}}
    elif attack == 'disabled': f.character['releaseReady'] = False
    elif attack == 'style-conflict': body['computeVisualClass'] = 'realistic'
    elif attack == 'raw-style-conflict': body['settings']['visualStyle'] = 'realistic'
    elif attack == 'extra-input': body['files'] = {'image2':{}}
    else: body['featureId'] = 'img2video'
    assert not f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['eligible']
    with pytest.raises(Exception):
        f.web.submit_autoip(f.web.owner, str(uuid.uuid4()), body, {'max_credits':40})
    assert not f.plans and not f.accepted and not f.stages


@pytest.mark.parametrize('change', [{'negative':'watermark'}, {'steps':26}, {'seed':42}, {'resolution':'2048'},
                                  {'instruction':'x'*1200}, {'guidance':2.5}])
def test_custom_creative_controls_are_never_dropped(character_web, change):
    f = character_web
    body = request()
    body['settings'].update(change)
    assert not f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['eligible']
    assert not f.plans


def test_non_square_photoshoot_is_explicitly_unavailable(character_web):
    from PIL import Image
    f = character_web
    Image.new('RGB',(8,16),'red').save(f.avatar)
    result = f.web.inspect_autoip(f.web.owner, request(), {'max_credits':40})
    assert result['reasons'] == ['geometry_unsupported'] and not f.plans


@pytest.mark.parametrize('change', ['missing-override','custom-seed','custom-acceleration','short-multi','long-description'])
def test_h3_requires_explicit_compatible_output_and_full_prose(character_web, change):
    f = character_web
    body = request(feature='h3-reference-video')
    if change == 'missing-override': body.pop('computeAutoipVideo')
    elif change == 'custom-seed': body['settings']['seed'] = 42
    elif change == 'custom-acceleration': body['settings']['acceleration'] = 'sage'
    elif change == 'short-multi': body['computeAutoipVideo']['duration_seconds'] = 2
    else: body['settings']['description'] = 'x'*1000
    assert not f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['eligible']
    assert not f.plans


def test_realistic_rights_and_fresh_price_are_still_authoritative(character_web):
    f = character_web
    f.character['visualClass'] = 'realistic'
    body = request('realistic')
    f.rights.revoked = True
    assert f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['reasons'] == ['capability_grant_denied']
    f.rights.revoked = False
    assert f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['eligible']
    f.cost = 41
    assert f.web.inspect_autoip(f.web.owner, body, {'max_credits':40})['reasons'] == ['budget_exceeded']
    with pytest.raises(Exception) as error:
        f.web.submit_autoip(f.web.owner, str(uuid.uuid4()), body, {'max_credits':40})
    assert error.value.code == 'budget_exceeded' and not f.accepted


def test_worker_rejects_avatar_replacement_against_signed_source(character_web):
    from provider_fabric.autoip import AutoIPFile
    from provider_fabric.autoip_worker import _verified_file
    from provider_fabric.provider_worker import DefiniteLocalFailure
    f = character_web
    assert f.web.inspect_autoip(f.web.owner, request(), {'max_credits':40})['eligible']
    asset = f.plans[-1].intent.assets[0]
    loader = lambda owner, canonical, role: AutoIPFile(role, f.avatar, content_type='image/png', visual_class=canonical.visual_class)
    _verified_file(loader, f.actor.user_id, asset, 'image')
    f.avatar.write_bytes(b'replaced')
    with pytest.raises(DefiniteLocalFailure):
        _verified_file(loader, f.actor.user_id, asset, 'image')


def test_origin_change_changes_signed_shot_binding(character_web):
    f = character_web
    rid = str(uuid.uuid4())
    before = f.web._autoip_character_plan(f.runtime, f.actor, request(), {'max_credits':40}, rid)
    from PIL import Image
    Image.new('RGB',(8,8),'red').save(f.avatar)
    after = f.web._autoip_character_plan(f.runtime, f.actor, request(), {'max_credits':40}, rid)
    assert before['origin']['sha256'] != after['origin']['sha256']
    assert before['plan']['creative_instruction']['shot_id'] != after['plan']['creative_instruction']['shot_id']


def test_router_keeps_original_tuple_and_replay_never_resubmits(character_web):
    f = character_web
    body, rid = request(), str(uuid.uuid4())
    first = f.web.router.submit(f.web.owner, rid, body, 'autoip', {'max_credits':40})
    second = f.web.router.submit(f.web.owner, rid, body, 'autoip', {'max_credits':40})
    assert first['downstream_id'] == second['downstream_id'] == 'intent:synthetic'
    assert len(f.accepted) == 1
    stored = json.loads(f.web.router._get(f.web.owner, rid)['request_json'])
    assert {key: stored[key] for key in ('characterId','action','featureId','variantId')} == {
        key: body[key] for key in ('characterId','action','featureId','variantId')}
    assert f.accepted[0]['plan']['feature_id'] != stored['featureId']
    changed = {**body, 'variantId':'other-variant'}
    with pytest.raises(Exception) as error:
        f.web.router.submit(f.web.owner, rid, changed, 'autoip', {'max_credits':40})
    assert error.value.code == 'request_conflict' and len(f.accepted) == 1


def test_changed_source_during_preview_is_not_reported_eligible(character_web):
    from PIL import Image
    f = character_web
    original = f.runtime.service.plan_intent
    def changed(actor, body):
        Image.new('RGB',(8,8),'red').save(f.avatar)
        return original(actor, body)
    f.runtime.service.plan_intent = changed
    result = f.web.inspect_autoip(f.web.owner, request(), {'max_credits':40})
    assert result['reasons'] == ['source_changed'] and not f.accepted


def test_changed_source_at_submission_rejects_without_acceptance(character_web):
    from PIL import Image
    f = character_web
    original = f.runtime.service.submit_generation
    def changed(actor, body, **kwargs):
        Image.new('RGB',(8,8),'red').save(f.avatar)
        return original(actor, body, **kwargs)
    f.runtime.service.submit_generation = changed
    with pytest.raises(Exception) as error:
        f.web.submit_autoip(f.web.owner, str(uuid.uuid4()), request(), {'max_credits':40})
    assert error.value.code == 'source_changed' and not f.accepted


def test_real_service_source_guard_is_before_confirm_and_replay_is_before_resolution(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT/'webui'))
    from provider_fabric.service import ActorContext, ProviderFabricService, ProviderServiceError
    service = object.__new__(ProviderFabricService)
    actor = ActorContext('owner-alice','member')
    request_body = {'schema_version':1, 'request_id':'generation:'+'a'*32, 'plan':{'max_credits':40}}
    expected = {'characters':[{'character_id':'hero'}], 'assets':[{'sha256':'a'*64}]}
    planned = {'intent':copy.deepcopy(expected), 'quote':{'credits':30}, 'plan_token':'synthetic', 'decision':{}}
    resolutions, confirmations = [], []
    replay = [None]
    service._generation_plan_nonce = lambda *args:'b'*32
    service._find_generation_replay = lambda *args: replay[0]
    def resolve(*args, **kwargs):
        resolutions.append(True)
        return copy.deepcopy(planned)
    service._plan_intent = resolve
    service.confirm_intent = lambda *args: confirmations.append(args) or {'ok':True}
    service._generation_result = lambda *args: {'intent_id':'intent:original'}
    wrong = copy.deepcopy(expected)
    wrong['assets'][0]['sha256'] = 'c'*64
    with pytest.raises(ProviderServiceError) as error:
        service.submit_generation(actor, request_body, expected_resources=wrong)
    assert error.value.code == 'source_changed' and not confirmations
    accepted = service.submit_generation(actor, request_body, expected_resources=expected)
    assert accepted == {'intent_id':'intent:original'} and len(confirmations) == 1
    replay[0] = accepted
    count = len(resolutions)
    assert service.submit_generation(actor, request_body, expected_resources=wrong) == accepted
    assert len(resolutions) == count and len(confirmations) == 1
    # The private guard is not an accepted HTTP JSON field.
    with pytest.raises(ProviderServiceError):
        service.submit_generation(actor, {**request_body, 'expected_resources':expected})


@pytest.mark.parametrize('style,kind', [('anime','ai_sister'), ('anime','bestie'), ('realistic','human')])
@pytest.mark.parametrize('feature', ['character-photoshoot','h3-image-video','h3-reference-video'])
def test_deployment_capability_builder_and_avatar_binding_exactly(character_web, style, kind, feature):
    """Also runnable in a disk harness linked to the private source checkout.

    This runs the actual deployment capability constructors and binding helper,
    not a fabricated v1 capability list. No production configuration is imported.
    """
    f = character_web
    tree = ast.parse((ROOT/'webui/server.py').read_text())
    functions = {node.name:node for node in tree.body if isinstance(node,ast.FunctionDef)}
    if 'append_reference_character_capabilities' not in functions:
        pytest.skip('deployment-specific capability builder is not in this source tree')
    names = {'_character_capability','append_reference_character_capabilities','character_capabilities',
             '_append_ready_minimax_h3_capabilities','minimax_h3_contract_for'}
    import minimax_h3_product as H3
    f.globals.update(finalize_character_capabilities=lambda entry,items,runtime:items,
        character_post_provider_id=lambda entry:None, REALISTIC_LIPSYNC_RELEASE_READY=False,
        REALISTIC_LIPSYNC_RELEASE_REASON='synthetic disabled', minimax_h3_character_variant=H3.character_variant,
        H3_FEATURE_CONTRACTS={}, H3_VARIANT_CONTRACTS={})
    nodes = [ast.ImportFrom(module='__future__',names=[ast.alias(name='annotations')],level=0)]
    nodes += [copy.deepcopy(functions[name]) for name in names]
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])), '<deployment capability builder>', 'exec'),f.globals)
    f.character.update(visualClass=style,kind=kind,actions=['photoshoot','image-video'])
    caps = f.globals['character_capabilities'](f.character)
    cap = next(item for item in caps if item['featureId']==feature)
    body = request(style,feature)
    body.update(action=cap['actionId'],variantId=cap['variantId'])
    if feature=='character-photoshoot':
        expected = ('photoshoot.anime.character-photoshoot.v3' if style=='anime'
                    else 'photoshoot.realistic.character-photoshoot.reference.v1')
        assert body['variantId']==expected
    if kind=='human':
        # The private source additionally requires operator authority for a
        # human character, even when the synthetic account owns its avatar.
        with pytest.raises(Exception):
            f.web._autoip_character_plan(f.runtime,f.actor,body,{'max_credits':40},str(uuid.uuid4()))
        f.user['role']='admin'
        f.actor=replace(f.actor,role='admin')
        f.globals['_PROVIDER_CANONICAL_CONTEXT'].actor=f.actor
    compiled = f.web._autoip_character_plan(f.runtime,f.actor,body,{'max_credits':40},str(uuid.uuid4()))
    assert compiled['origin']['variant_id']==cap['variantId']
    assert compiled['origin']['action_id']==cap['actionId']
    assert compiled['plan']['assets'][0]['asset_id']==f'castle-character:{f.character["id"]}:avatar'
    assert compiled['plan']['assets'][0]['sha256']==hashlib.sha256(f.media).hexdigest()
    assert f.web.inspect_autoip(f.web.owner,body,{'max_credits':40})['eligible']
    f.web.submit_autoip(f.web.owner,str(uuid.uuid4()),body,{'max_credits':40})
    assert len(f.accepted)==1
