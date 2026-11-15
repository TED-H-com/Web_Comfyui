"""Release-reviewed aliases; never read user configuration or mutate a bundle."""
import copy
import hashlib
import json
from pathlib import Path

VERSION = '20261111-r29b'
MAP_SHA256 = '794080f7fac08301095d29bb1ea0653b9a216eb8a821e9434ef4c2ce035550fb'
SOURCE = 'Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors'

def reviewed_map():
    from .contracts import fail
    try:
        raw = Path(__file__).with_name('hosted-model-aliases-r29b.json').read_bytes()
    except OSError:
        fail('model_alias_evidence_missing', '模型別名證據檔案缺失')
    if hashlib.sha256(raw).hexdigest() != MAP_SHA256:
        fail('model_alias_evidence_missing', '模型別名證據缺失或已變更')
    return json.loads(raw)['aliases']

def resolve(bundle, lane, name):
    from .contracts import fail
    if lane not in {'runninghub', 'comfy_cloud'} or name != SOURCE:
        return None
    entry = reviewed_map().get(lane, {}).get(name)
    if not entry or not entry.get('evidence'):
        fail('model_alias_evidence_missing', '模型別名缺少已審查證據')
    model = next((m for m in bundle['models'] if m['name'] == name), {})
    if model.get('sha256') != entry['source_sha256'] or model.get('bytes') != entry['bytes']:
        fail('model_alias_hash_mismatch', '來源模型與別名證據不一致')
    if lane == 'runninghub':
        proof = bundle.get('hosted_model_proof', {}).get(name, {})
        if any(proof.get(k) != entry['evidence'][k] for k in ('md5', 'page_id')):
            fail('model_alias_evidence_missing', 'RunningHub 別名缺少 MD5 證據')
    return {'map_version': VERSION, **copy.deepcopy(entry)}

def apply(bundle, lane, graph, model_fields):
    records = []
    for node in graph.values():
        for field in model_fields:
            name = node['inputs'].get(field)
            if not isinstance(name, str): continue
            entry = resolve(bundle, lane, name)
            if entry:
                node['inputs'][field] = entry['target_name']
                if entry not in records: records.append(entry)
    # Validate manifest aliases even if a loader is absent from this graph.
    for model in bundle['models']:
        resolve(bundle, lane, model['name'])
    return records
