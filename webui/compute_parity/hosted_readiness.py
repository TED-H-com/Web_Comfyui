"""Read-only provider inventory refresh; optional persistence of local evidence only."""
import time
from urllib.parse import urlencode
from .contracts import compile_request, dependency_probe, number, fail, ParityError
from .readiness import match, save
from .transport import Transport
from .hosted_estimates import estimate
from compute_parity.model_aliases import resolve, VERSION

PUBLIC_MD5 = {
 'sam3.1_multiplex_fp16.safetensors': ('2046067224942743554', '91f0b5ae94a056996b0d7c3ddf035b10'),
 'wan2.1_14B_SCAIL_2_fp8_scaled.safetensors': ('2064356307234283522', 'cf34dfd1c0d024753c7b916666c9326a'),
 'Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors': ('1945379281314750465', '2d59a66a1a8bbfa4d3abc65105167fe2'),
}
LORA_SHA = '8833bd4fd7c8eabebf0bc8ee5cfaf47f4f310ce116928a02c1adf8941dd4b0f1'

def refresh(console, bundle, lane, estimates=None, *, persist=True):
    if lane not in {'runninghub', 'comfy_cloud'}: fail('lane_invalid', '未知 hosted 平台')
    if bundle['feature'] not in {'dance', 'video-replace'}: fail('voice_node_contract_required', '共用環境尚缺聲音節點')
    owner = console.owner; root = console.root / 'compute-parity'
    with console.prefs.db() as db: data = console._raw(lane, db)
    if lane=='runninghub':
        from .runninghub_native import require_membership
        require_membership(data.get('account_type'))
    entry = {'owner': owner, 'lane': lane, 'case_id': bundle['case_id'], 'bundle_sha256': bundle['bundle_sha256'],
             'credential_version': data['credential_version'], 'observed_at': time.time(), 'expires_at': time.time() + 86400,
             'dependencies_ready': False, 'alias_map_version': VERSION, 'alias_evidence_verified': False,
             'model_aliases': [], 'quote': {}, 'reason_code': 'parity_probe_quote_required', 'verification_note': ''}
    try:
        plan = compile_request(bundle, lane, request_id='00000000-0000-0000-0000-000000000000')
    except ParityError as error:
        if not error.code.startswith('model_alias_'): raise
        entry.update(reason_code=error.code, verification_note='模型別名證據未通過，尚未就緒')
        if persist: save(root, entry)
        return {'lane': lane, 'can_enable': False, 'ready': False, 'reason': error.code, 'quote': {}, 'evidence': entry}
    transport = Transport.for_owner(console, owner, lane, enabled=True)
    info = transport.request('GET', '/object_info' if lane == 'runninghub' else '/api/object_info')
    graph = plan.get('probe_graph') or plan['json']['prompt']
    allowed, private_checks = set(), []
    if lane == 'comfy_cloud':
        from .cloud_inventory import private_aliases
        allowed, private_checks = private_aliases(bundle, graph, transport)
    probe = dependency_probe(graph, info, model_inventory=allowed)
    entry.update(account_fingerprint=transport.fingerprint, dependency_probe=probe,
                 dependencies_ready=probe['ready'], alias_evidence_verified=True, model_aliases=plan['model_aliases'])
    if lane == 'runninghub':
        proof = bundle.get('hosted_model_proof', {})
        valid = all(proof.get(name, {}).get('md5') == md5 and proof.get(name, {}).get('page_id') == page
                    for name, (page, md5) in PUBLIC_MD5.items())
        entry.update(hashes_verified=valid, hash_evidence=proof,
                     hash_coverage='three_required_favourites; existing stock loaders retained',
                     lifecycle_mode='provisional_monitored', lifecycle_verified=False,
                     verification_note='三個收藏模型 MD5 已驗；既有內建模型沿用。首筆完成後監測 POINT +5/+15/+30/+60 分鐘')
    else:
        checks = list(private_checks)
        for model in bundle['models']:
            alias = resolve(bundle, lane, model['name'])
            if alias: continue  # private model already checked with provenance and category
            name = model['name']
            # The LoRA must be in this account's private asset inventory.
            rows = transport.request('GET', '/api/assets?' + urlencode({'name_contains': name,
                'include_public': 'false' if alias else 'true', 'limit': 100})).get('assets', [])
            found = any(a.get('name') == name and a.get('size') == model['bytes'] for a in rows)
            checks.append({'source_name': model['name'], 'target_name': name, 'bytes': model['bytes'], 'present': found})
        lora = next((m for m in bundle['models'] if m['name'].startswith('Wan21_I2V')), {})
        entry.update(dependencies_ready=probe['ready'] and all(c['present'] for c in checks), model_inventory=checks,
                     hashes_verified=False, hash_mode='name_size_provisional', lora_sha_verified=lora.get('sha256') == LORA_SHA,
                     verification_note='內建模型同名同大小、hash 未驗；私人 LoRA provenance-verified（固定 HF SHA＋精確大小，遠端 hash 未提供）')
    entry['connection_verified'] = True  # authenticated live inventory, scoped to credential_version
    previous = match(root, owner, lane, data['credential_version'], bundle['bundle_sha256'])
    if previous:
        entry['quote'] = previous.get('quote', {})
        if previous.get('lifecycle_mode') in {'stopped_drain', 'stopped_observation_gap', 'lifecycle_verified'}:
            for key in ('lifecycle_mode', 'lifecycle_verified', 'lifecycle_observations'): entry[key] = previous.get(key)
    from .hosted_estimates import measurement_from_journal
    generated = estimate(bundle, lane, measurement=measurement_from_journal(root/'journal.sqlite3',owner,bundle))
    if generated: entry['quote'] = generated
    amount = (estimates or {}).get('runninghub_points' if lane == 'runninghub' else 'comfy_cloud_credits')
    if amount is not None:
        if not number(amount) or amount <= 0: fail('quote_invalid', '平台估價必須大於零')
        entry['quote'] = {'unit': 'POINT' if lane == 'runninghub' else 'credits', 'amount': amount,
                          'basis': 'owner_provider_estimate', 'is_estimate': True, 'label': '擁有者估計，非官方報價'}
    if entry['quote']:
        q = entry['quote']
        entry['verification_note'] += '；' + q.get('label', '估計，非官方報價') + f"：{q['amount']:.3f} {q['unit']}"
    from .readiness import dependencies_usable, usable
    ready = usable(entry, lane)
    entry['reason_code'] = None if ready else 'parity_probe_quote_required'
    if persist: save(root, entry)
    return {'lane': lane, 'can_enable': dependencies_usable(entry, lane), 'ready': ready, 'quote': entry['quote'],
            'reason': entry['reason_code'], 'verification_note': entry['verification_note'], 'model_aliases': entry['model_aliases'],
            'evidence': entry}
