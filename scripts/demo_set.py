"""Load and validate the closed-loop documentation demo set."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

SUBJECT_KEYS = {
    'id', 'name_zh', 'name_en', 'gender', 'age', 'domain', 'listed',
    'role_zh', 'role_en', 'description_zh', 'description_en',
    'tags_zh', 'tags_en', 'base', 'portrait', 'character_id',
}
MATERIAL_KEYS = {
    'id', 'file', 'thumb', 'poster', 'kind', 'folder', 'library', 'subject',
    'domain', 'label_zh', 'label_en', 'url_alias', 'produced_by',
    'bytes', 'width', 'height', 'duration_seconds', 'transcript', 'gender',
}
CASE_KEYS = {
    'id', 'feature_id', 'kind', 'engine', 'display', 'order',
    'title_zh', 'title_en', 'subjects', 'domain', 'inputs', 'settings',
    'outputs', 'comparison', 'run_id', 'stand_in_for', 'audio', 'post',
    'note_zh', 'note_en', 'disclosure_zh', 'disclosure_en',
}
OUTPUT_KEYS = {'file', 'kind', 'poster', 'thumb', 'material'}

STATUSES = {'final', 'placeholder'}
GENDERS = {'female', 'male'}
DOMAINS = {'real', 'anime'}
MEDIA_KINDS = {'image', 'video', 'audio'}
FOLDERS = {'people', 'scenes', 'videos', 'voices'}
CASE_KINDS = {'recorded', 'prepared', 'interface-simulation'}
ENGINES = {'comfyui', 'voxcpm2', 'none'}
ACCEPT_KIND = {'image/*': 'image', 'video/*': 'video', 'audio/*': 'audio'}

DOMAIN_EXEMPT_SLOTS = {
    'dance': {'video'},
    'h3-reference-video': {'video1', 'audio1'},
    'lipsync': {'voice'},
}
VOICE_FEATURES = {'voice-speak', 'voxcpm-tts', 'voice-batch', 'voice-clone'}


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _is_nonempty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _is_opt_nonempty_str(value: Any) -> bool:
    return value is None or _is_nonempty_str(value)


def _is_str_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_nonempty_str(item) for item in value)


def load_demo_set(root: Path) -> dict:
    """Read `webui/docs/demo-set.json` under *root*."""
    path = Path(root) / 'webui' / 'docs' / 'demo-set.json'
    return json.loads(path.read_text(encoding='utf-8'))


def _validate_structure(data: dict, errors: list[str]) -> tuple[dict[str, dict], dict[str, dict], dict[str, dict]]:
    sv = data.get('schema_version')
    if not _is_int(sv) or sv != 1:
        errors.append('schema_version must be integer 1')
    status = data.get('status')
    if status not in STATUSES:
        errors.append(f'status must be one of {sorted(STATUSES)}')

    subjects = data.get('subjects')
    materials = data.get('materials')
    cases = data.get('cases')
    if not isinstance(subjects, list):
        errors.append('subjects must be a list')
        subjects = []
    if not isinstance(materials, list):
        errors.append('materials must be a list')
        materials = []
    if not isinstance(cases, list):
        errors.append('cases must be a list')
        cases = []

    subjects_by_id: dict[str, dict] = {}
    for index, subj in enumerate(subjects):
        if not isinstance(subj, dict):
            errors.append(f'subjects[{index}] must be an object')
            continue
        missing = sorted(SUBJECT_KEYS - subj.keys())
        if missing:
            errors.append(f'subject {subj.get("id", index)} missing keys: {missing}')
            continue
        sid = subj['id']
        if not _is_nonempty_str(sid):
            errors.append(f'subjects[{index}].id must be non-empty string')
            continue
        if sid in subjects_by_id:
            errors.append(f'duplicate subject id: {sid}')
        else:
            subjects_by_id[sid] = subj
        for field in ('name_zh', 'name_en', 'role_zh', 'role_en', 'description_zh', 'description_en'):
            if not _is_nonempty_str(subj[field]):
                errors.append(f'subject {sid}.{field} must be non-empty string')
        for field in ('tags_zh', 'tags_en'):
            if not _is_str_list(subj[field]):
                errors.append(f'subject {sid}.{field} must be list of non-empty strings')
        if subj['gender'] not in GENDERS:
            errors.append(f'subject {sid}.gender invalid: {subj["gender"]!r}')
        if subj['age'] is not None and not _is_int(subj['age']):
            errors.append(f'subject {sid}.age must be int or null')
        if subj['domain'] not in DOMAINS:
            errors.append(f'subject {sid}.domain invalid: {subj["domain"]!r}')
        if not isinstance(subj['listed'], bool):
            errors.append(f'subject {sid}.listed must be bool')
        for field in ('base', 'portrait'):
            if not _is_opt_nonempty_str(subj[field]):
                errors.append(f'subject {sid}.{field} must be string or null')
        cid = subj['character_id']
        if cid is not None and cid != f'docs-character-{sid}':
            errors.append(f'subject {sid}.character_id must be null or docs-character-{sid}')

    materials_by_id: dict[str, dict] = {}
    for index, mat in enumerate(materials):
        if not isinstance(mat, dict):
            errors.append(f'materials[{index}] must be an object')
            continue
        missing = sorted(MATERIAL_KEYS - mat.keys())
        if missing:
            errors.append(f'material {mat.get("id", index)} missing keys: {missing}')
            continue
        mid = mat['id']
        if not _is_nonempty_str(mid):
            errors.append(f'materials[{index}].id must be non-empty string')
            continue
        if mid in materials_by_id:
            errors.append(f'duplicate material id: {mid}')
        else:
            materials_by_id[mid] = mat
        for field in ('file', 'label_zh', 'label_en'):
            if not _is_nonempty_str(mat[field]):
                errors.append(f'material {mid}.{field} must be non-empty string')
        for field in ('thumb', 'poster', 'subject', 'url_alias', 'produced_by', 'transcript'):
            if not _is_opt_nonempty_str(mat[field]):
                errors.append(f'material {mid}.{field} must be string or null')
        if mat['kind'] not in MEDIA_KINDS:
            errors.append(f'material {mid}.kind invalid: {mat["kind"]!r}')
        if mat['folder'] is not None and mat['folder'] not in FOLDERS:
            errors.append(f'material {mid}.folder invalid: {mat["folder"]!r}')
        if not isinstance(mat['library'], bool):
            errors.append(f'material {mid}.library must be bool')
        if mat['domain'] is not None and mat['domain'] not in DOMAINS:
            errors.append(f'material {mid}.domain invalid: {mat["domain"]!r}')
        if not _is_int(mat['bytes']) or mat['bytes'] < 0:
            errors.append(f'material {mid}.bytes must be non-negative int')
        for field in ('width', 'height'):
            if mat[field] is not None and (not _is_int(mat[field]) or mat[field] <= 0):
                errors.append(f'material {mid}.{field} must be positive int or null')
        if mat['duration_seconds'] is not None and (not _is_number(mat['duration_seconds']) or mat['duration_seconds'] < 0):
            errors.append(f'material {mid}.duration_seconds must be non-negative number or null')
        if mat['gender'] is not None and mat['gender'] not in GENDERS:
            errors.append(f'material {mid}.gender invalid: {mat["gender"]!r}')

    cases_by_id: dict[str, dict] = {}
    for index, case in enumerate(cases):
        if not isinstance(case, dict):
            errors.append(f'cases[{index}] must be an object')
            continue
        missing = sorted(CASE_KEYS - case.keys())
        if missing:
            errors.append(f'case {case.get("id", index)} missing keys: {missing}')
            continue
        cid = case['id']
        if not _is_nonempty_str(cid):
            errors.append(f'cases[{index}].id must be non-empty string')
            continue
        if cid in cases_by_id:
            errors.append(f'duplicate case id: {cid}')
        else:
            cases_by_id[cid] = case
        for field in ('feature_id', 'title_zh', 'title_en'):
            if not _is_nonempty_str(case[field]):
                errors.append(f'case {cid}.{field} must be non-empty string')
        if case['kind'] not in CASE_KINDS:
            errors.append(f'case {cid}.kind invalid: {case["kind"]!r}')
        if case['engine'] not in ENGINES:
            errors.append(f'case {cid}.engine invalid: {case["engine"]!r}')
        if not isinstance(case['display'], bool):
            errors.append(f'case {cid}.display must be bool')
        else:
            if case['display'] and not _is_int(case['order']):
                errors.append(f'case {cid}.order must be int when display is true')
            if not case['display'] and case['order'] is not None:
                errors.append(f'case {cid}.order must be null when display is false')
        if not _is_str_list(case['subjects']):
            errors.append(f'case {cid}.subjects must be list of strings')
        if case['domain'] is not None and case['domain'] not in DOMAINS:
            errors.append(f'case {cid}.domain invalid: {case["domain"]!r}')
        if not isinstance(case['inputs'], dict) or not all(
            _is_nonempty_str(k) and _is_nonempty_str(v) for k, v in case['inputs'].items()
        ):
            errors.append(f'case {cid}.inputs must map slot strings to material id strings')
        if not isinstance(case['settings'], dict):
            errors.append(f'case {cid}.settings must be an object')
        if not isinstance(case['outputs'], list):
            errors.append(f'case {cid}.outputs must be a list')
        else:
            for out_idx, out in enumerate(case['outputs']):
                if not isinstance(out, dict):
                    errors.append(f'case {cid}.outputs[{out_idx}] must be an object')
                    continue
                out_missing = sorted(OUTPUT_KEYS - out.keys())
                if out_missing:
                    errors.append(f'case {cid}.outputs[{out_idx}] missing keys: {out_missing}')
                    continue
                if not _is_nonempty_str(out['file']):
                    errors.append(f'case {cid}.outputs[{out_idx}].file must be non-empty string')
                if out['kind'] not in MEDIA_KINDS:
                    errors.append(f'case {cid}.outputs[{out_idx}].kind invalid: {out["kind"]!r}')
                for field in ('poster', 'thumb', 'material'):
                    if not _is_opt_nonempty_str(out[field]):
                        errors.append(f'case {cid}.outputs[{out_idx}].{field} must be string or null')
        comp = case['comparison']
        if comp is not None and (not isinstance(comp, dict) or not _is_nonempty_str(comp.get('before'))):
            errors.append(f'case {cid}.comparison must be null or object with non-empty "before"')
        for field in ('run_id', 'stand_in_for', 'note_zh', 'note_en', 'disclosure_zh', 'disclosure_en'):
            if not _is_opt_nonempty_str(case[field]):
                errors.append(f'case {cid}.{field} must be non-empty string or null')
        if not _is_str_list(case['audio']):
            errors.append(f'case {cid}.audio must be list of strings')
        post = case['post']
        if post is not None:
            if not isinstance(post, dict) or not _is_nonempty_str(post.get('text_zh')) or not _is_nonempty_str(post.get('text_en')):
                errors.append(f'case {cid}.post must be null or object with text_zh and text_en')

    return subjects_by_id, materials_by_id, cases_by_id


def _validate_assets(
    materials_by_id: dict[str, dict],
    cases_by_id: dict[str, dict],
    assets: dict[str, str],
    examples_dir: Path,
    errors: list[str],
) -> None:
    examples_root = Path(examples_dir)
    digest_cache: dict[str, str | None] = {}

    def check_file(owner: str, field: str, fname: str | None) -> None:
        if fname is None:
            return
        if fname not in assets:
            errors.append(f'{owner}.{field} "{fname}" is not in PUBLIC_DEMO_ASSETS')
            return
        if fname not in digest_cache:
            target = examples_root / fname
            if not target.is_file() or target.is_symlink():
                digest_cache[fname] = None
            else:
                digest_cache[fname] = hashlib.sha256(target.read_bytes()).hexdigest()
        actual = digest_cache[fname]
        if actual is None:
            errors.append(f'{owner}.{field} "{fname}" missing under {examples_root}')
        elif actual != assets[fname]:
            errors.append(f'{owner}.{field} "{fname}" sha256 mismatch: {actual} != {assets[fname]}')

    for mid, mat in materials_by_id.items():
        for field in ('file', 'thumb', 'poster'):
            check_file(f'material {mid}', field, mat.get(field))
    for cid, case in cases_by_id.items():
        outputs = case.get('outputs')
        if not isinstance(outputs, list):
            continue
        for idx, out in enumerate(outputs):
            if not isinstance(out, dict):
                continue
            for field in ('file', 'thumb', 'poster'):
                check_file(f'case {cid}.outputs[{idx}]', field, out.get(field))


def validate_demo_set(
    data: dict,
    *,
    assets: dict[str, str],
    provenance: dict,
    examples_dir: Path,
    app_guide: dict | None = None,
) -> list[str]:
    """Validate *data* against section 6.3 and return error strings (empty when valid)."""
    if not isinstance(data, dict):
        return ['demo-set root must be an object']

    errors: list[str] = []
    subjects_by_id, materials_by_id, cases_by_id = _validate_structure(data, errors)

    # Rule 2: asset membership and sha256 match
    _validate_assets(materials_by_id, cases_by_id, assets, examples_dir, errors)

    # Rule 3: material/subject references and app_guide upload slot kinds
    features_by_id = (
        {feat['id']: feat for feat in app_guide.get('features', []) if isinstance(feat, dict) and 'id' in feat}
        if isinstance(app_guide, dict)
        else {}
    )
    for sid, subj in subjects_by_id.items():
        for field in ('base', 'portrait'):
            mid = subj.get(field)
            if mid is not None and mid not in materials_by_id:
                errors.append(f'subject {sid}.{field} references unknown material {mid!r}')
    for mid, mat in materials_by_id.items():
        subj_id = mat.get('subject')
        if subj_id is not None and subj_id not in subjects_by_id:
            errors.append(f'material {mid}.subject references unknown subject {subj_id!r}')

    runs_by_id = {
        row['id']: row for row in provenance.get('runs', []) if isinstance(row, dict) and 'id' in row
    }
    audio_run_ids = {
        row['id'] for row in provenance.get('audio_runs', []) if isinstance(row, dict) and 'id' in row
    }
    provenance_assets = {
        row['file']: row for row in provenance.get('assets', []) if isinstance(row, dict) and 'file' in row
    }

    for cid, case in cases_by_id.items():
        for sid in case.get('subjects', []):
            if sid not in subjects_by_id:
                errors.append(f'case {cid}.subjects references unknown subject {sid!r}')
        inputs = case.get('inputs', {})
        feature = features_by_id.get(case.get('feature_id'))
        uploads_by_id = (
            {u['id']: u for u in feature.get('uploads', []) if isinstance(u, dict) and 'id' in u}
            if feature is not None
            else None
        )
        if isinstance(inputs, dict):
            for slot, mid in inputs.items():
                if mid not in materials_by_id:
                    errors.append(f'case {cid}.inputs[{slot!r}] references unknown material {mid!r}')
                    continue
                if uploads_by_id is not None:
                    if slot not in uploads_by_id:
                        errors.append(f'case {cid}.inputs[{slot!r}] is not an upload slot of {case["feature_id"]}')
                    else:
                        expected_kind = ACCEPT_KIND.get(uploads_by_id[slot].get('accept', ''))
                        actual_kind = materials_by_id[mid].get('kind')
                        if expected_kind and actual_kind != expected_kind:
                            errors.append(
                                f'case {cid}.inputs[{slot!r}] material {mid} kind {actual_kind!r} != {expected_kind!r}'
                            )

        outputs = case.get('outputs', [])
        if isinstance(outputs, list):
            for idx, out in enumerate(outputs):
                if isinstance(out, dict) and out.get('material') is not None:
                    if out['material'] not in materials_by_id:
                        errors.append(f'case {cid}.outputs[{idx}].material unknown: {out["material"]!r}')

        # Rule 6: audio ids
        for aid in case.get('audio', []):
            if aid not in materials_by_id:
                errors.append(f'case {cid}.audio references unknown material {aid!r}')
            else:
                amat = materials_by_id[aid]
                if amat.get('kind') != 'audio':
                    errors.append(f'case {cid}.audio material {aid} kind must be audio')
                if amat.get('produced_by') not in audio_run_ids:
                    errors.append(f'case {cid}.audio material {aid} produced_by {amat.get("produced_by")!r} not in audio_runs')

        # Rule 4: recorded cases provenance checks
        kind = case.get('kind')
        if kind == 'recorded':
            run_id = case.get('run_id')
            run = runs_by_id.get(run_id) if isinstance(run_id, str) else None
            if run is None:
                errors.append(f'recorded case {cid}.run_id {run_id!r} not found in provenance runs')
            else:
                if run.get('status') != 'success':
                    errors.append(f'recorded case {cid} run {run_id} status must be success')
                if run.get('feature_id') != case.get('feature_id'):
                    errors.append(
                        f'recorded case {cid} run {run_id} feature_id {run.get("feature_id")!r} != {case.get("feature_id")!r}'
                    )
                run_files = {o.get('file') for o in run.get('outputs', []) if isinstance(o, dict) and o.get('file')}
                if isinstance(outputs, list):
                    for idx, out in enumerate(outputs):
                        if not isinstance(out, dict):
                            continue
                        ofile = out.get('file')
                        if ofile not in run_files:
                            errors.append(f'recorded case {cid}.outputs[{idx}] file {ofile!r} not produced by run {run_id}')
                        for aux_key in ('poster', 'thumb'):
                            aux = out.get(aux_key)
                            if aux is None:
                                continue
                            asset_meta = provenance_assets.get(aux)
                            derived = asset_meta.get('derived_from') if asset_meta else None
                            if isinstance(derived, str):
                                derived_list = [derived]
                            elif isinstance(derived, list):
                                derived_list = derived
                            else:
                                derived_list = []
                            if asset_meta is None or ofile not in derived_list:
                                errors.append(
                                    f'recorded case {cid}.outputs[{idx}].{aux_key} {aux!r} must be derived_from {ofile!r} in provenance assets'
                                )

        # Rule 5 & Rule 7: prepared cases
        if kind == 'prepared':
            if not _is_nonempty_str(case.get('disclosure_zh')):
                errors.append(f'prepared case {cid}.disclosure_zh must be non-empty string')
            if not _is_nonempty_str(case.get('disclosure_en')):
                errors.append(f'prepared case {cid}.disclosure_en must be non-empty string')
            target_id = case.get('stand_in_for')
            expected_outputs: list[dict[str, Any]] | None = []
            if target_id is not None:
                target = cases_by_id.get(target_id)
                if target is None:
                    errors.append(f'prepared case {cid}.stand_in_for {target_id!r} does not exist')
                    expected_outputs = None
                elif target.get('kind') != 'recorded':
                    errors.append(f'prepared case {cid}.stand_in_for {target_id} is not recorded')
                    expected_outputs = None
                else:
                    expected_outputs = list(target.get('outputs') or [])
            if expected_outputs is not None:
                audio_ids = case.get('audio')
                if isinstance(audio_ids, list):
                    for aid in audio_ids:
                        amat = materials_by_id.get(aid) if isinstance(aid, str) else None
                        if amat is None:
                            expected_outputs = None
                            break
                        expected_outputs.append({
                            'file': amat['file'],
                            'kind': 'audio',
                            'poster': None,
                            'thumb': None,
                            'material': aid,
                        })
                else:
                    expected_outputs = None
            if expected_outputs is not None and case.get('outputs') != expected_outputs:
                if target_id is not None:
                    errors.append(f'prepared case {cid} outputs must equal target case {target_id} plus audio outputs')
                else:
                    errors.append(f'prepared case {cid} outputs must match section 6.1 audio outputs mapping')

        # Rule 8: post-generator cases
        if case.get('feature_id') == 'post-generator':
            settings = case.get('settings')
            if not isinstance(settings, dict) or settings.get('mode') != 'quick':
                errors.append(f'post-generator case {cid}.settings.mode must equal "quick"')
            if not isinstance(settings, dict) or not _is_nonempty_str(settings.get('quick')):
                errors.append(f'post-generator case {cid}.settings.quick must be non-empty string')
            if case.get('outputs') != []:
                errors.append(f'post-generator case {cid}.outputs must be []')
            post = case.get('post')
            if not isinstance(post, dict) or not _is_nonempty_str(post.get('text_zh')) or not _is_nonempty_str(post.get('text_en')):
                errors.append(f'post-generator case {cid}.post must have non-empty text_zh and text_en')

        # Rule 9: voice-speak cases
        if case.get('feature_id') == 'voice-speak':
            settings = case.get('settings')
            if not isinstance(settings, dict) or settings.get('voiceSource') != 'library':
                errors.append(f'voice-speak case {cid}.settings.voiceSource must equal "library"')
            voice_id = settings.get('voiceId') if isinstance(settings, dict) else None
            vmat = materials_by_id.get(voice_id) if isinstance(voice_id, str) else None
            if vmat is None or vmat.get('kind') != 'audio' or vmat.get('library') is not True:
                errors.append(f'voice-speak case {cid}.settings.voiceId {voice_id!r} must be an audio material with library=true')
            elif settings.get('sampleText') != vmat.get('transcript'):
                errors.append(f'voice-speak case {cid}.settings.sampleText must equal voiceId material transcript')

    # Rules 10, 11, 12, 13: final validation only
    if data.get('status') == 'final':
        if app_guide is None:
            errors.append('app_guide required for final validation')
        else:
            displayed_by_feature: dict[str, list[dict]] = {}
            for case in cases_by_id.values():
                if case.get('display') is True:
                    displayed_by_feature.setdefault(case['feature_id'], []).append(case)

            # Rule 10: every key of app_guide['demos'] has >= 1 displayed case
            for demo_key in app_guide.get('demos', {}):
                if not displayed_by_feature.get(demo_key):
                    errors.append(f'final status missing displayed case for demo {demo_key!r}')

            # Rule 11: gender rule
            for feature_id, displayed_cases in displayed_by_feature.items():
                subj_genders = {
                    subjects_by_id[sid]['gender']
                    for c in displayed_cases
                    for sid in c.get('subjects', [])
                    if sid in subjects_by_id and subjects_by_id[sid].get('gender') in GENDERS
                }
                if subj_genders and subj_genders != GENDERS:
                    errors.append(f'gender rule violated for feature {feature_id}: subjects have {sorted(subj_genders)}')
                if feature_id in VOICE_FEATURES:
                    audio_genders = {
                        materials_by_id[aid]['gender']
                        for c in displayed_cases
                        for aid in c.get('audio', [])
                        if aid in materials_by_id and materials_by_id[aid].get('gender') in GENDERS
                    }
                    if audio_genders != GENDERS:
                        errors.append(f'gender rule violated for voice feature {feature_id}: audio has {sorted(audio_genders)}')

            # Rule 13: library voices -> voice-speak & listed subjects -> character-voice
            voice_speak_counts: dict[str, int] = {}
            for c in displayed_by_feature.get('voice-speak', []):
                vid = c.get('settings', {}).get('voiceId') if isinstance(c.get('settings'), dict) else None
                if isinstance(vid, str):
                    voice_speak_counts[vid] = voice_speak_counts.get(vid, 0) + 1
            for mid, mat in materials_by_id.items():
                if mat.get('kind') == 'audio' and mat.get('library') is True:
                    if voice_speak_counts.get(mid, 0) != 1:
                        errors.append(
                            f'library audio material {mid!r} must be settings.voiceId of exactly one displayed voice-speak case (got {voice_speak_counts.get(mid, 0)})'
                        )

            cv_subjects = {
                sid
                for c in displayed_by_feature.get('character-voice', [])
                for sid in (c.get('subjects') or [])
                if isinstance(sid, str)
            }
            for sid, subj in subjects_by_id.items():
                if subj.get('listed') is True and sid not in cv_subjects:
                    errors.append(f'listed subject {sid!r} missing displayed character-voice case')

        # Rule 11: domain rule within every case
        for cid, case in cases_by_id.items():
            fid = case.get('feature_id', '')
            exempt = DOMAIN_EXEMPT_SLOTS.get(fid, set())
            domains: set[str] = set()
            if case.get('domain') in DOMAINS:
                domains.add(case['domain'])
            inputs = case.get('inputs', {})
            if isinstance(inputs, dict):
                for slot, mid in inputs.items():
                    if slot in exempt or mid not in materials_by_id:
                        continue
                    mat = materials_by_id[mid]
                    if mat.get('kind') in {'image', 'video'} and mat.get('domain') in DOMAINS:
                        domains.add(mat['domain'])
            outputs = case.get('outputs', [])
            if isinstance(outputs, list):
                for out in outputs:
                    if not isinstance(out, dict):
                        continue
                    omid = out.get('material')
                    if out.get('kind') in {'image', 'video'} and omid in materials_by_id:
                        odom = materials_by_id[omid].get('domain')
                        if odom in DOMAINS:
                            domains.add(odom)
            if len(domains) > 1:
                errors.append(f'domain rule violated in case {cid}: mixed domains {sorted(domains)}')

        # Rule 12: every material used by at least one case or listed subject's base/portrait
        used_materials: set[str] = set()
        for subj in subjects_by_id.values():
            if subj.get('listed') is True:
                for field in ('base', 'portrait'):
                    if subj.get(field):
                        used_materials.add(subj[field])
        for case in cases_by_id.values():
            if isinstance(case.get('inputs'), dict):
                used_materials.update(case['inputs'].values())
            used_materials.update(case.get('audio', []))
            if isinstance(case.get('outputs'), list):
                for out in case['outputs']:
                    if isinstance(out, dict) and out.get('material'):
                        used_materials.add(out['material'])
        unused = sorted(set(materials_by_id) - used_materials)
        if unused:
            errors.append(f'unused materials in final demo set: {unused}')

    return errors
