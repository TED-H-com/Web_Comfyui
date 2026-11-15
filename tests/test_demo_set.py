"""Unit tests for the closed-loop documentation demo set validator."""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import app_guide_catalog, demo_catalog, demo_set, public_demo_assets


def _real_inputs():
    ag = app_guide_catalog.catalog(ROOT)
    ag['demos'] = demo_catalog.demos(ag)
    provenance = json.loads((ROOT / 'webui/docs/examples/provenance.json').read_text(encoding='utf-8'))
    return {
        'assets': dict(public_demo_assets.PUBLIC_DEMO_ASSETS),
        'provenance': provenance,
        'examples_dir': ROOT / 'webui/docs/examples',
        'app_guide': ag,
    }


def _prepared_case(
    case_id: str,
    feature_id: str,
    stand_in_for: str | None,
    *,
    subjects=None,
    domain='real',
    inputs=None,
    settings=None,
    audio=None,
    outputs=None,
    post=None,
    order=1,
):
    audio_list = list(audio) if audio is not None else []
    return {
        'id': case_id,
        'feature_id': feature_id,
        'kind': 'prepared',
        'engine': 'voxcpm2' if audio_list else 'none',
        'display': True,
        'order': order,
        'title_zh': f'{case_id} zh',
        'title_en': f'{case_id} en',
        'subjects': subjects if subjects is not None else ['real-f', 'real-m'],
        'domain': domain,
        'inputs': inputs if inputs is not None else {},
        'settings': copy.deepcopy(settings) if settings is not None else {},
        'outputs': copy.deepcopy(outputs) if outputs is not None else (
            [
                {
                    'file': 'cheer-subject-blend.webp',
                    'kind': 'image',
                    'poster': None,
                    'thumb': None,
                    'material': None,
                }
            ] if stand_in_for else []
        ),
        'comparison': None,
        'run_id': None,
        'stand_in_for': stand_in_for,
        'audio': audio_list,
        'post': copy.deepcopy(post) if post is not None else None,
        'note_zh': None,
        'note_en': None,
        'disclosure_zh': '預製示範案例說明。',
        'disclosure_en': 'Prepared demo disclosure.',
    }


def _make_valid_final_set(base_app_guide: dict):
    """Build a minimal structurally and semantically valid status=='final' set."""
    prov = json.loads((ROOT / 'webui/docs/examples/provenance.json').read_text(encoding='utf-8'))
    audio_runs = list(prov.get('audio_runs', []))
    if len(audio_runs) < 2:
        raise AssertionError('Need at least 2 provenance audio runs')

    extra_audio_run = {'id': 'test-male-audio-run', 'status': 'success', 'engine': 'VoxCPM2', 'device': 'CPU'}
    prov = {**prov, 'audio_runs': [*audio_runs, extra_audio_run]}

    data = {
        'schema_version': 1,
        'status': 'final',
        'subjects': [
            {
                'id': 'real-f',
                'name_zh': '小晴',
                'name_en': 'Real F',
                'gender': 'female',
                'age': 26,
                'domain': 'real',
                'listed': True,
                'role_zh': '女角',
                'role_en': 'Woman',
                'description_zh': '成年女性虛構角色。',
                'description_en': 'Adult female fictional subject.',
                'tags_zh': ['真人'],
                'tags_en': ['Realistic'],
                'base': 'mat-img',
                'portrait': None,
                'character_id': 'docs-character-real-f',
            },
            {
                'id': 'real-m',
                'name_zh': '阿遠',
                'name_en': 'Real M',
                'gender': 'male',
                'age': 30,
                'domain': 'real',
                'listed': True,
                'role_zh': '男角',
                'role_en': 'Man',
                'description_zh': '成年男性虛構角色。',
                'description_en': 'Adult male fictional subject.',
                'tags_zh': ['真人'],
                'tags_en': ['Realistic'],
                'base': 'mat-img',
                'portrait': None,
                'character_id': 'docs-character-real-m',
            },
        ],
        'materials': [
            {
                'id': 'mat-img',
                'file': 'cheer-subject-reference.webp',
                'thumb': None,
                'poster': None,
                'kind': 'image',
                'folder': 'people',
                'library': True,
                'subject': 'real-f',
                'domain': 'real',
                'label_zh': '人物圖',
                'label_en': 'Person image',
                'url_alias': None,
                'produced_by': None,
                'bytes': 1274512,
                'width': 1024,
                'height': 1536,
                'duration_seconds': None,
                'transcript': None,
                'gender': None,
            },
            {
                'id': 'mat-scene',
                'file': 'stadium-ground-v2-reference.webp',
                'thumb': None,
                'poster': None,
                'kind': 'image',
                'folder': 'scenes',
                'library': True,
                'subject': None,
                'domain': 'real',
                'label_zh': '球場',
                'label_en': 'Stadium',
                'url_alias': None,
                'produced_by': None,
                'bytes': 1173444,
                'width': 1024,
                'height': 1536,
                'duration_seconds': None,
                'transcript': None,
                'gender': None,
            },
            {
                'id': 'mat-vid',
                'file': 'cheer-wave.mp4',
                'thumb': None,
                'poster': 'cheer-wave-poster.webp',
                'kind': 'video',
                'folder': 'videos',
                'library': True,
                'subject': 'real-f',
                'domain': 'real',
                'label_zh': '揮手影片',
                'label_en': 'Wave clip',
                'url_alias': 'https://video.example.com/cheer-wave.mp4',
                'produced_by': None,
                'bytes': 1310381,
                'width': 480,
                'height': 720,
                'duration_seconds': 5.0625,
                'transcript': None,
                'gender': None,
            },
            {
                'id': 'mat-audio-f',
                'file': 'warm-welcome.wav',
                'thumb': None,
                'poster': None,
                'kind': 'audio',
                'folder': 'voices',
                'library': True,
                'subject': None,
                'domain': None,
                'label_zh': '女聲',
                'label_en': 'Female voice',
                'url_alias': None,
                'produced_by': 'voxcpm2-warm-welcome',
                'bytes': 537644,
                'width': None,
                'height': None,
                'duration_seconds': 5.6,
                'transcript': '你好',
                'gender': 'female',
            },
            {
                'id': 'mat-audio-m',
                'file': 'game-cheer.wav',
                'thumb': None,
                'poster': None,
                'kind': 'audio',
                'folder': 'voices',
                'library': True,
                'subject': None,
                'domain': None,
                'label_zh': '男聲',
                'label_en': 'Male voice',
                'url_alias': None,
                'produced_by': 'test-male-audio-run',
                'bytes': 721964,
                'width': None,
                'height': None,
                'duration_seconds': 7.52,
                'transcript': '準備出發',
                'gender': 'male',
            },
        ],
        'cases': [
            {
                'id': 'rec-blend',
                'feature_id': 'image-blend',
                'kind': 'recorded',
                'engine': 'comfyui',
                'display': True,
                'order': 1,
                'title_zh': '融合案例',
                'title_en': 'Blend case',
                'subjects': ['real-f', 'real-m'],
                'domain': 'real',
                'inputs': {'image1': 'mat-img', 'image2': 'mat-scene'},
                'settings': {'positive': 'person', 'threshold': 0.5},
                'outputs': [
                    {
                        'file': 'cheer-subject-blend.webp',
                        'kind': 'image',
                        'poster': None,
                        'thumb': None,
                        'material': None,
                    }
                ],
                'comparison': {'before': 'image1'},
                'run_id': 'cheer-subject-blend-v4',
                'stand_in_for': None,
                'audio': [],
                'post': None,
                'note_zh': None,
                'note_en': None,
                'disclosure_zh': None,
                'disclosure_en': None,
            },
            {
                'id': 'rec-lipsync',
                'feature_id': 'lipsync',
                'kind': 'recorded',
                'engine': 'comfyui',
                'display': True,
                'order': 1,
                'title_zh': '對嘴案例',
                'title_en': 'Lipsync case',
                'subjects': ['real-f', 'real-m'],
                'domain': 'real',
                'inputs': {'video': 'mat-vid', 'voice': 'mat-audio-f'},
                'settings': {},
                'outputs': [
                    {
                        'file': 'cheer-lipsync.mp4',
                        'kind': 'video',
                        'poster': 'cheer-lipsync-poster.webp',
                        'thumb': None,
                        'material': None,
                    }
                ],
                'comparison': None,
                'run_id': 'cheer-lipsync-v1',
                'stand_in_for': None,
                'audio': [],
                'post': None,
                'note_zh': None,
                'note_en': None,
                'disclosure_zh': None,
                'disclosure_en': None,
            },
        ],
    }
    mats_by_id = {m['id']: m for m in data['materials']}

    def _audio_outs(*mids):
        return [
            {'file': mats_by_id[mid]['file'], 'kind': 'audio', 'poster': None, 'thumb': None, 'material': mid}
            for mid in mids
        ]

    covered = {'image-blend', 'lipsync'}
    for key in base_app_guide['demos']:
        if key in covered:
            continue
        if key == 'voice-speak':
            for idx, (sid, aid) in enumerate((('real-f', 'mat-audio-f'), ('real-m', 'mat-audio-m')), 1):
                data['cases'].append(
                    _prepared_case(
                        f'prep-voice-speak-{idx}',
                        'voice-speak',
                        None,
                        subjects=[sid],
                        domain=None,
                        settings={
                            'voiceSource': 'library',
                            'voiceId': aid,
                            'sampleText': mats_by_id[aid]['transcript'],
                        },
                        audio=[aid],
                        outputs=_audio_outs(aid),
                        order=idx,
                    )
                )
        elif key in demo_set.VOICE_FEATURES:
            data['cases'].append(
                _prepared_case(
                    f'prep-{key}',
                    key,
                    None,
                    subjects=['real-f', 'real-m'],
                    domain=None,
                    audio=['mat-audio-f', 'mat-audio-m'],
                    outputs=_audio_outs('mat-audio-f', 'mat-audio-m'),
                )
            )
        elif key == 'post-generator':
            data['cases'].append(
                _prepared_case(
                    'prep-post-generator',
                    'post-generator',
                    None,
                    subjects=['real-f', 'real-m'],
                    domain='real',
                    settings={'mode': 'quick', 'quick': '球場午後隨拍'},
                    outputs=[],
                    post={'text_zh': '中文貼文內容', 'text_en': 'English post text'},
                )
            )
        elif key == 'qwen21-text2img':
            data['cases'].append(_prepared_case(f'prep-{key}', key, 'rec-blend', subjects=[], domain='real'))
        else:
            data['cases'].append(_prepared_case(f'prep-{key}', key, 'rec-blend', subjects=['real-f', 'real-m'], domain='real'))

    data['cases'].append(
        _prepared_case(
            'prep-cv-f',
            'character-voice',
            None,
            subjects=['real-f'],
            domain='real',
            audio=['mat-audio-f'],
            outputs=_audio_outs('mat-audio-f'),
            order=1,
        )
    )
    data['cases'].append(
        _prepared_case(
            'prep-cv-m',
            'character-voice',
            None,
            subjects=['real-m'],
            domain='real',
            audio=['mat-audio-m'],
            outputs=_audio_outs('mat-audio-m'),
            order=2,
        )
    )
    return data, prov


def test_placeholder_demo_set_passes_structural_validation():
    ctx = _real_inputs()
    data = demo_set.load_demo_set(ROOT)
    assert data['status'] == 'placeholder'
    assert demo_set.validate_demo_set(data, **ctx) == []
    assert demo_set.validate_demo_set(
        data,
        assets=ctx['assets'],
        provenance=ctx['provenance'],
        examples_dir=ctx['examples_dir'],
        app_guide=None,
    ) == []


def test_rule1_schema_status_uniqueness_keys_text_and_order():
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    bad_sv = copy.deepcopy(base)
    bad_sv['schema_version'] = True
    assert demo_set.validate_demo_set(bad_sv, **ctx)

    bad_status = copy.deepcopy(base)
    bad_status['status'] = 'draft'
    assert demo_set.validate_demo_set(bad_status, **ctx)

    dup_mat = copy.deepcopy(base)
    dup_mat['materials'].append(copy.deepcopy(dup_mat['materials'][0]))
    assert any('duplicate material id' in err for err in demo_set.validate_demo_set(dup_mat, **ctx))

    missing_key = copy.deepcopy(base)
    del missing_key['materials'][0]['transcript']
    assert any('missing keys' in err for err in demo_set.validate_demo_set(missing_key, **ctx))

    empty_text = copy.deepcopy(base)
    empty_text['cases'][0]['title_zh'] = '   '
    assert any('title_zh' in err for err in demo_set.validate_demo_set(empty_text, **ctx))

    bad_order = copy.deepcopy(base)
    bad_order['cases'][0]['display'] = False
    assert any('order must be null' in err for err in demo_set.validate_demo_set(bad_order, **ctx))


def test_rule2_assets_membership_and_sha256(tmp_path):
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    unknown_file = copy.deepcopy(base)
    unknown_file['materials'][0]['file'] = 'not-in-public-assets.webp'
    assert any('PUBLIC_DEMO_ASSETS' in err for err in demo_set.validate_demo_set(unknown_file, **ctx))

    tampered = copy.deepcopy(ctx['examples_dir'])
    temp_examples = tmp_path / 'examples'
    temp_examples.mkdir()
    for fname in ctx['assets']:
        src = tampered / fname
        if src.is_file():
            (temp_examples / fname).write_bytes(b'corrupted' if fname == 'cheer-subject-reference.webp' else src.read_bytes())
    errs = demo_set.validate_demo_set(
        base,
        assets=ctx['assets'],
        provenance=ctx['provenance'],
        examples_dir=temp_examples,
        app_guide=ctx['app_guide'],
    )
    assert any('sha256 mismatch' in err for err in errs)


def test_rule3_references_and_app_guide_upload_slots():
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    bad_mat_ref = copy.deepcopy(base)
    bad_mat_ref['subjects'][0]['base'] = 'missing-mat'
    assert any('unknown material' in err for err in demo_set.validate_demo_set(bad_mat_ref, **ctx))

    bad_subj_ref = copy.deepcopy(base)
    bad_subj_ref['cases'][0]['subjects'] = ['missing-subj']
    assert any('unknown subject' in err for err in demo_set.validate_demo_set(bad_subj_ref, **ctx))

    bad_slot = copy.deepcopy(base)
    bad_slot['cases'][0]['inputs'] = {'nonexistent_slot': 'ph-cheer'}
    assert any('not an upload slot' in err for err in demo_set.validate_demo_set(bad_slot, **ctx))

    wrong_kind = copy.deepcopy(base)
    wrong_kind['cases'][0]['inputs']['image1'] = 'ph-warm'
    assert any('kind' in err for err in demo_set.validate_demo_set(wrong_kind, **ctx))

    cc_case = _prepared_case(
        'cc-test', 'character-create', 'ph-blend-case',
        subjects=['cheer'], inputs={'arbitrary': 'ph-cheer'}, outputs=base['cases'][0]['outputs'],
    )
    with_cc = copy.deepcopy(base)
    with_cc['cases'].append(cc_case)
    assert demo_set.validate_demo_set(with_cc, **ctx) == []


def test_rule4_recorded_case_provenance_checks():
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    bad_run = copy.deepcopy(base)
    bad_run['cases'][0]['run_id'] = 'unknown-run'
    assert any('not found in provenance' in err for err in demo_set.validate_demo_set(bad_run, **ctx))

    bad_feat = copy.deepcopy(base)
    prov_copy = copy.deepcopy(ctx['provenance'])
    for row in prov_copy['runs']:
        if row['id'] == 'cheer-subject-blend-v4':
            row['feature_id'] = 'text2img'
    assert any('feature_id' in err for err in demo_set.validate_demo_set(
        bad_feat, assets=ctx['assets'], provenance=prov_copy, examples_dir=ctx['examples_dir'], app_guide=ctx['app_guide']
    ))

    bad_status = copy.deepcopy(ctx['provenance'])
    for row in bad_status['runs']:
        if row['id'] == 'cheer-subject-blend-v4':
            row['status'] = 'error'
    assert any('status must be success' in err for err in demo_set.validate_demo_set(
        base, assets=ctx['assets'], provenance=bad_status, examples_dir=ctx['examples_dir'], app_guide=ctx['app_guide']
    ))

    bad_output = copy.deepcopy(base)
    bad_output['cases'][0]['outputs'][0]['file'] = 'mountain-teahouse.webp'
    assert any('not produced by run' in err for err in demo_set.validate_demo_set(bad_output, **ctx))

    bad_poster = copy.deepcopy(ctx['provenance'])
    for asset in bad_poster['assets']:
        if asset['file'] == 'cheer-lipsync-poster.webp':
            asset['derived_from'] = ['cheer-wave.mp4']
    assert any('derived_from' in err for err in demo_set.validate_demo_set(
        base, assets=ctx['assets'], provenance=bad_poster, examples_dir=ctx['examples_dir'], app_guide=ctx['app_guide']
    ))


def test_rule5_6_7_prepared_stand_in_audio_and_disclosure():
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    # Valid prepared stand-in passes
    good = copy.deepcopy(base)
    good['cases'].append(_prepared_case(
        'prep-ok', 'anime-scene-card', 'ph-blend-case', subjects=['cheer'], outputs=base['cases'][0]['outputs'],
    ))
    assert demo_set.validate_demo_set(good, **ctx) == []

    # Rule 5: wrong outputs relative to target + audio outputs
    bad_outputs = copy.deepcopy(good)
    bad_outputs['cases'][-1]['outputs'][0]['material'] = None
    assert any('must equal target case' in err for err in demo_set.validate_demo_set(bad_outputs, **ctx))

    bad_extra_audio = copy.deepcopy(base)
    bad_extra_audio['cases'].append(_prepared_case(
        'prep-bad-audio-map', 'h3-reference-video', 'ph-blend-case',
        subjects=['cheer'], audio=['ph-warm'], outputs=base['cases'][0]['outputs'],
    ))
    assert any('plus audio outputs' in err for err in demo_set.validate_demo_set(bad_extra_audio, **ctx))

    bad_no_standin = copy.deepcopy(base)
    bad_no_standin['cases'].append(_prepared_case(
        'prep-missing-aud-out', 'voice-clone', None, subjects=['cheer'], domain=None, audio=['ph-warm'], outputs=[],
    ))
    assert any('section 6.1 audio outputs mapping' in err for err in demo_set.validate_demo_set(bad_no_standin, **ctx))

    # Rule 5: target is not recorded
    bad_target = copy.deepcopy(good)
    bad_target['cases'].append(_prepared_case('prep-chain', 'img2img', 'prep-ok', subjects=['cheer']))
    assert any('not recorded' in err for err in demo_set.validate_demo_set(bad_target, **ctx))

    # Rule 6: audio id material produced_by must be in audio_runs
    bad_audio = copy.deepcopy(base)
    warm_mat = next(m for m in bad_audio['materials'] if m['id'] == 'ph-warm')
    bad_audio['materials'].append({
        **warm_mat,
        'id': 'ph-bad-audio',
        'produced_by': 'not-an-audio-run',
    })
    bad_audio['cases'].append(_prepared_case(
        'prep-aud', 'voice-clone', None, subjects=['cheer'], domain=None, audio=['ph-bad-audio'],
        outputs=[{'file': warm_mat['file'], 'kind': 'audio', 'poster': None, 'thumb': None, 'material': 'ph-bad-audio'}],
    ))
    assert any('audio_runs' in err for err in demo_set.validate_demo_set(bad_audio, **ctx))

    # Rule 7: missing disclosure on prepared
    no_disc = copy.deepcopy(good)
    no_disc['cases'][-1]['disclosure_en'] = None
    assert any('disclosure_en' in err for err in demo_set.validate_demo_set(no_disc, **ctx))


def test_rules8_and_9_post_generator_and_voice_speak():
    ctx = _real_inputs()
    base = demo_set.load_demo_set(ROOT)

    # Rule 8: post-generator checks
    bad_pg_mode = copy.deepcopy(base)
    pg_case = next(c for c in bad_pg_mode['cases'] if c['feature_id'] == 'post-generator')
    pg_case['settings']['mode'] = 'advanced'
    assert any('settings.mode must equal "quick"' in err for err in demo_set.validate_demo_set(bad_pg_mode, **ctx))

    bad_pg_quick = copy.deepcopy(base)
    pg_case = next(c for c in bad_pg_quick['cases'] if c['feature_id'] == 'post-generator')
    pg_case['settings']['quick'] = '  '
    assert any('settings.quick must be non-empty' in err for err in demo_set.validate_demo_set(bad_pg_quick, **ctx))

    bad_pg_out = copy.deepcopy(base)
    pg_case = next(c for c in bad_pg_out['cases'] if c['feature_id'] == 'post-generator')
    pg_case['outputs'] = copy.deepcopy(bad_pg_out['cases'][0]['outputs'])
    assert any('outputs must be []' in err for err in demo_set.validate_demo_set(bad_pg_out, **ctx))

    bad_pg_post = copy.deepcopy(base)
    pg_case = next(c for c in bad_pg_post['cases'] if c['feature_id'] == 'post-generator')
    pg_case['post']['text_en'] = ''
    assert any('non-empty text_zh and text_en' in err for err in demo_set.validate_demo_set(bad_pg_post, **ctx))

    # Rule 9: voice-speak checks
    bad_vs_src = copy.deepcopy(base)
    vs_case = next(c for c in bad_vs_src['cases'] if c['feature_id'] == 'voice-speak')
    vs_case['settings']['voiceSource'] = 'persona'
    assert any('settings.voiceSource must equal "library"' in err for err in demo_set.validate_demo_set(bad_vs_src, **ctx))

    bad_vs_lib = copy.deepcopy(base)
    vs_case = next(c for c in bad_vs_lib['cases'] if c['feature_id'] == 'voice-speak')
    vs_case['settings']['voiceId'] = 'ph-clone'  # audio material with library=False
    vs_case['settings']['sampleText'] = next(m for m in bad_vs_lib['materials'] if m['id'] == 'ph-clone')['transcript']
    assert any('library=true' in err for err in demo_set.validate_demo_set(bad_vs_lib, **ctx))

    bad_vs_text = copy.deepcopy(base)
    vs_case = next(c for c in bad_vs_text['cases'] if c['feature_id'] == 'voice-speak')
    vs_case['settings']['sampleText'] = 'wrong transcript'
    assert any('sampleText must equal voiceId material transcript' in err for err in demo_set.validate_demo_set(bad_vs_text, **ctx))


def test_rules10_11_12_13_final_validation():
    ctx = _real_inputs()
    final_data, final_prov = _make_valid_final_set(ctx['app_guide'])

    # Baseline final fixture is valid
    assert demo_set.validate_demo_set(
        final_data,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    ) == []

    # Rule 8: app_guide=None rejected
    errs = demo_set.validate_demo_set(
        final_data,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=None,
    )
    assert errs == ['app_guide required for final validation']

    # Rule 8: missing displayed case for a demo key
    missing_demo = copy.deepcopy(final_data)
    missing_demo['cases'] = [c for c in missing_demo['cases'] if c['feature_id'] != 'qwen21-edit']
    errs = demo_set.validate_demo_set(
        missing_demo,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any('qwen21-edit' in err for err in errs)

    # Rule 9: domain rule fails on non-exempt mismatch, passes on exempt dance.video
    mixed_domain = copy.deepcopy(final_data)
    anime_subj = copy.deepcopy(mixed_domain['subjects'][0])
    anime_subj['id'] = 'anime-f'
    anime_subj['domain'] = 'anime'
    anime_subj['listed'] = False
    anime_subj['character_id'] = None
    anime_mat = copy.deepcopy(mixed_domain['materials'][0])
    anime_mat['id'] = 'mat-anime-img'
    anime_mat['subject'] = 'anime-f'
    anime_mat['domain'] = 'anime'
    mixed_domain['subjects'].append(anime_subj)
    mixed_domain['materials'].append(anime_mat)
    # Use anime character + real video in non-exempt slot for face-swap
    for c in mixed_domain['cases']:
        if c['feature_id'] == 'face-swap':
            c['domain'] = 'real'
            c['inputs'] = {'video': 'mat-vid', 'face': 'mat-anime-img'}
    errs = demo_set.validate_demo_set(
        mixed_domain,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any('domain rule violated' in err for err in errs)

    exempt_ok = copy.deepcopy(final_data)
    exempt_ok['subjects'].append(anime_subj)
    exempt_ok['materials'].append(anime_mat)
    for c in exempt_ok['cases']:
        if c['feature_id'] == 'dance':
            c['domain'] = 'anime'
            c['inputs'] = {'reference': 'mat-anime-img', 'video': 'mat-vid'}
    assert demo_set.validate_demo_set(
        exempt_ok,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    ) == []

    # Rule 11: gender rule (subjects + voice audio)
    one_gender = copy.deepcopy(final_data)
    mats_by_id = {m['id']: m for m in one_gender['materials']}
    for c in one_gender['cases']:
        if c['feature_id'] == 'img2img':
            c['subjects'] = ['real-f']
        if c['feature_id'] == 'voice-speak':
            c['audio'] = ['mat-audio-f']
            c['outputs'] = [{
                'file': mats_by_id['mat-audio-f']['file'],
                'kind': 'audio',
                'poster': None,
                'thumb': None,
                'material': 'mat-audio-f',
            }]
    errs = demo_set.validate_demo_set(
        one_gender,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any('gender rule violated for feature img2img' in err for err in errs)
    assert any('gender rule violated for voice feature voice-speak' in err for err in errs)

    # Rule 12: unused material in final set
    unused_mat = copy.deepcopy(final_data)
    unused_mat['materials'].append({**unused_mat['materials'][0], 'id': 'mat-unused'})
    errs = demo_set.validate_demo_set(
        unused_mat,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any('unused materials' in err for err in errs)

    # Rule 13: every library audio must have exactly 1 displayed voice-speak case
    missing_vs_for_lib = copy.deepcopy(final_data)
    missing_vs_for_lib['cases'] = [c for c in missing_vs_for_lib['cases'] if c['id'] != 'prep-voice-speak-2']
    errs = demo_set.validate_demo_set(
        missing_vs_for_lib,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any("library audio material 'mat-audio-m'" in err for err in errs)

    dup_vs_for_lib = copy.deepcopy(final_data)
    dup_case = copy.deepcopy(next(c for c in dup_vs_for_lib['cases'] if c['id'] == 'prep-voice-speak-1'))
    dup_case['id'] = 'prep-voice-speak-dup'
    dup_case['order'] = 3
    dup_vs_for_lib['cases'].append(dup_case)
    errs = demo_set.validate_demo_set(
        dup_vs_for_lib,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any("library audio material 'mat-audio-f'" in err for err in errs)

    # Rule 13: every listed subject must have a displayed character-voice case
    missing_cv = copy.deepcopy(final_data)
    missing_cv['cases'] = [c for c in missing_cv['cases'] if c['id'] != 'prep-cv-m']
    errs = demo_set.validate_demo_set(
        missing_cv,
        assets=ctx['assets'],
        provenance=final_prov,
        examples_dir=ctx['examples_dir'],
        app_guide=ctx['app_guide'],
    )
    assert any("listed subject 'real-m' missing displayed character-voice case" in err for err in errs)
