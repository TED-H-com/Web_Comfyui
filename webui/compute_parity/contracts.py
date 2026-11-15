"""Shared native contracts, with web-only hosted model evidence and aliases.

Local/Colab/AutoIP delegate to the shared compiler; r30 repins its runner sources.
"""
from scripts.colab_remote.native_contracts import *
from scripts.colab_remote.native_contracts import compile_request as _native_compile, dependency_probe as _native_probe

def dependency_probe(graph, object_info, *, model_inventory=None):
    """Compare model combo inputs as well as node classes; no guessing from templates."""
    if not model_inventory: return _native_probe(graph, object_info)
    missing_nodes, missing_models, missing_inputs = set(), set(), set()
    for n in graph.values():
        cls = n['class_type']; info = object_info.get(cls)
        if not isinstance(info, dict): missing_nodes.add(cls); continue
        fields = schema_fields(info,n['inputs'])
        missing_inputs.update(cls+'.'+key for key in missing_required(info,n['inputs']))
        for key, value in n['inputs'].items():
            if key not in fields: missing_inputs.add(cls + '.' + key); continue
            if key in MODEL_FIELDS:
                spec = fields[key]
                options=spec[0] if isinstance(spec,(list,tuple)) and spec and isinstance(spec[0],list) else spec[1].get('options',[]) if isinstance(spec,(list,tuple)) and len(spec)>1 and isinstance(spec[1],dict) else []
                if value not in options and not (isinstance(value, str) and (cls, key, value) in model_inventory):
                    missing_models.add(str(value))
    ready = not (missing_nodes or missing_models or missing_inputs)
    return {'ready': ready, 'missing_nodes':sorted(missing_nodes), 'missing_models':sorted(missing_models),
            'missing_inputs':sorted(missing_inputs), 'hashes_verified':False,
            'reason': '' if ready else '初始化缺件：模型需先在你的 RunningHub 帳號收藏；缺少節點或欄位亦須先補齊'}


def compile_request(bundle, lane, **kwargs):
    if lane not in {'runninghub', 'comfy_cloud'}:
        return _native_compile(bundle, lane, **kwargs)
    from .model_aliases import apply
    validate_bundle(bundle)
    # Resolve against the immutable source manifest before the native compiler's
    # older RunningHub loader adaptation. The reviewed map owns final filenames.
    aliased = copy.deepcopy(bundle['graph'])
    records = apply(bundle, lane, aliased, MODEL_FIELDS)
    plan = _native_compile(bundle, lane, **kwargs)
    graph = plan.get('probe_graph') or plan['json']['prompt']
    for key, node in bundle['graph'].items():
        for field in MODEL_FIELDS:
            if isinstance(node['inputs'].get(field), str) and node['inputs'][field] != aliased[key]['inputs'][field]:
                graph[key]['inputs'][field] = aliased[key]['inputs'][field]
    plan['model_aliases'] = records
    plan['substitutions'] = [s for s in plan['substitutions'] if s.get('model') != 'lightx2v']
    plan['substitutions'].extend({'model_alias': entry} for entry in records)
    return plan
