"""Cloud private model inventory supplements the public-only object_info enums.

Contract: Comfy-Org/ComfyUI_frontend src/platform/assets/services/assetService.ts,
getAssetModels: model category + loader_path (fallback metadata.filename).
Only release-reviewed aliases with exact size and pinned import provenance qualify.
"""
from urllib.parse import urlencode
from compute_parity.model_aliases import resolve

def private_aliases(bundle, graph, transport):
    allowed = set(); records = []
    for model in bundle['models']:
        alias = resolve(bundle, 'comfy_cloud', model['name'])
        if not alias: continue
        name = alias['target_name']; proof = alias['evidence']
        rows = transport.request('GET', '/api/assets?' + urlencode({
            'name_contains': name, 'include_public': 'false', 'limit': 100})).get('assets', [])
        def verified(a):
            meta = a.get('metadata') or {}
            return (a.get('name') == name and a.get('size') == alias['bytes']
                    and {'models', 'loras'} <= set(a.get('tags') or [])
                    and (a.get('loader_path') or meta.get('filename')) == name
                    and meta.get('revision') == proof['revision']
                    and meta.get('resolve_url') == proof['url'])
        found = any(verified(a) for a in rows)
        records.append({'source_name': model['name'], 'target_name': name, 'bytes': alias['bytes'],
                        'present': found, 'verification': 'provenance_verified' if found else 'missing_or_unverified',
                        'inventory_source': 'private assets; models/loras; loader_path; pinned revision/resolve_url'})
        if found:
            for node in graph.values():
                if node['class_type'] in {'LoraLoader', 'LoraLoaderModelOnly'} and node['inputs'].get('lora_name') == name:
                    allowed.add((node['class_type'], 'lora_name', name))
    return allowed, records
