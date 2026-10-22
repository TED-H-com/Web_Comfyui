"""Static app-form documentation. Never imports/evaluates the HTTP server.

Only a small AST interpreter reads registry literals and presentation helpers.
No Python eval/exec, imports, filesystem calls, environment access or builder
execution are permitted in source expressions. Unknown constructs fail closed.
"""
from __future__ import annotations

import ast
import json
import operator
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_FILES = (
    'webui/server.py', 'webui/minimax_h3_native.py',
    'webui/minimax_h3_product.py', 'webui/qwen_image21.py',
    'manifests/app_guides/catalog.json',
    'workflows/minimax_h3/official/video_minimax_h3_t2v.json',
    'workflows/minimax_h3/official/video_minimax_h3_i2v.json',
    'workflows/minimax_h3/official/video_minimax_h3_r2v.json',
    'workflows/qwen_image21/image_qwen_image_2_1_t2i.json',
    'workflows/qwen_image21/image_qwen_image_2_1_image_edit.json',
)


def source_path(root, relative):
    """Only the declared source files, with no symlink components, may be read."""
    root = Path(root).resolve()
    if relative not in SOURCE_FILES:
        raise ValueError('Undeclared app guide source')
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in [path, *path.parents] if p != root and p.is_relative_to(root)):
        raise ValueError('App guide source must not be a symlink')
    if not path.is_file():
        raise ValueError('Missing app guide source: ' + relative)
    return path

PURE = {'list': list, 'tuple': tuple, 'str': str, 'int': int, 'len': len,
        'range': range, 'isinstance': isinstance, 'dict': dict}
BINARY = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul,
          ast.Div: operator.truediv, ast.Pow: operator.pow, ast.LShift: operator.lshift}
COMPARE = {ast.Eq: operator.eq, ast.NotEq: operator.ne, ast.In: lambda a,b: a in b,
           ast.NotIn: lambda a,b: a not in b}
METHODS = {dict: {'get', 'setdefault'}, list: {'append', 'extend'}, str: {'startswith'}}


class Returned(Exception):
    def __init__(self, value): self.value = value


class StaticSource:
    """Interpret only explicitly selected registry functions and their constants."""
    def __init__(self, path, *, functions=(), inherited=None):
        self.tree = ast.parse(Path(path).read_text(encoding='utf-8'))
        self.constants = {}
        self.functions = {}
        self.values = dict(inherited or {})
        self.resolving = set()
        for node in self.tree.body:
            if isinstance(node, (ast.Assign, ast.AnnAssign)):
                target = node.targets[0] if isinstance(node, ast.Assign) else node.target
                if isinstance(target, ast.Name): self.constants[target.id] = node.value
            elif isinstance(node, ast.FunctionDef) and node.name in functions:
                self.functions[node.name] = node

    def name(self, name, scope):
        if name in scope: return scope[name]
        if name in self.values: return self.values[name]
        if name in PURE: return PURE[name]
        if name not in self.constants or name in self.resolving:
            raise ValueError(f'Unsupported static name: {name}')
        self.resolving.add(name)
        try:
            node = self.constants[name]
            # These form limits use deployment environment overrides at runtime.
            # Documentation uses the literal code default, never the environment.
            if name in {'VOICE_SPEAK_MAX_CHARS', 'VOICE_CLONE_MAX_CHARS', 'VOICE_SAMPLE_MAX_CHARS'}:
                if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'int'
                        and len(node.args) == 1 and isinstance(node.args[0], ast.Call)
                        and ast.unparse(node.args[0].func) == 'os.environ.get'
                        and len(node.args[0].args) == 2):
                    raise ValueError('Voice limit default source changed')
                value = int(ast.literal_eval(node.args[0].args[1]))
            else:
                value = self.expr(node, {})
            self.values[name] = value
            return value
        finally:
            self.resolving.remove(name)

    def expr(self, node, scope):
        e = lambda n: self.expr(n, scope)
        if isinstance(node, ast.Constant): return node.value
        if isinstance(node, ast.Name): return self.name(node.id, scope)
        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            values=[]
            for child in node.elts:
                if isinstance(child, ast.Starred): values.extend(e(child.value))
                else: values.append(e(child))
            return tuple(values) if isinstance(node, ast.Tuple) else set(values) if isinstance(node, ast.Set) else values
        if isinstance(node, ast.Dict):
            out={}
            for key,value in zip(node.keys,node.values):
                if key is None: out.update(e(value))
                else: out[e(key)]=e(value)
            return out
        if isinstance(node, ast.Subscript): return e(node.value)[e(node.slice)]
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub): return -e(node.operand)
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not): return not e(node.operand)
        if isinstance(node, ast.BinOp) and type(node.op) in BINARY: return BINARY[type(node.op)](e(node.left),e(node.right))
        if isinstance(node, ast.IfExp): return e(node.body if e(node.test) else node.orelse)
        if isinstance(node, ast.BoolOp):
            value=e(node.values[0])
            for part in node.values[1:]:
                if isinstance(node.op, ast.And) and not value: break
                if isinstance(node.op, ast.Or) and value: break
                value=e(part)
            return value
        if isinstance(node, ast.Compare):
            left=e(node.left)
            for op,right_node in zip(node.ops,node.comparators):
                right=e(right_node)
                if type(op) not in COMPARE or not COMPARE[type(op)](left,right): return False
                left=right
            return True
        if isinstance(node, ast.JoinedStr): return ''.join(str(e(v)) for v in node.values)
        if isinstance(node, ast.FormattedValue):
            value=e(node.value)
            if node.conversion == 114: value=repr(value)
            elif node.conversion == 115: value=str(value)
            elif node.conversion not in (-1,): raise ValueError('Unsupported format conversion')
            return format(value,e(node.format_spec) if node.format_spec else '')
        if isinstance(node, ast.ListComp):
            if len(node.generators) != 1: raise ValueError('Nested comprehension unsupported')
            gen=node.generators[0]
            if gen.is_async: raise ValueError('Async not permitted')
            out=[]
            for value in e(gen.iter):
                inner=dict(scope); self.assign(gen.target,value,inner)
                if all(self.expr(test,inner) for test in gen.ifs): out.append(self.expr(node.elt,inner))
            return out
        if isinstance(node, ast.Call):
            args=[e(arg) for arg in node.args]
            if node.keywords: raise ValueError('Keyword calls not permitted')
            if isinstance(node.func, ast.Name):
                name=node.func.id
                if name in PURE: return PURE[name](*args)
                if name in self.functions: return self.call(name,args)
            elif isinstance(node.func, ast.Attribute):
                obj=e(node.func.value)
                if node.func.attr in METHODS.get(type(obj),set()):
                    return getattr(obj,node.func.attr)(*args)
            raise ValueError('Unapproved static call: '+ast.unparse(node.func))
        raise ValueError('Unsupported static expression: '+ast.dump(node)[:160])

    def assign(self,target,value,scope):
        if isinstance(target,ast.Name): scope[target.id]=value
        elif isinstance(target,(ast.Tuple,ast.List)):
            for child,item in zip(target.elts,value): self.assign(child,item,scope)
        elif isinstance(target,ast.Subscript): self.expr(target.value,scope)[self.expr(target.slice,scope)]=value
        else: raise ValueError('Unsupported assignment')

    def statements(self,nodes,scope):
        for node in nodes:
            if isinstance(node,ast.Assign):
                value=self.expr(node.value,scope)
                for target in node.targets:self.assign(target,value,scope)
            elif isinstance(node,ast.AugAssign) and isinstance(node.op,ast.Add):
                self.assign(node.target,self.expr(node.target,scope)+self.expr(node.value,scope),scope)
            elif isinstance(node,ast.Expr): self.expr(node.value,scope)
            elif isinstance(node,ast.If): self.statements(node.body if self.expr(node.test,scope) else node.orelse,scope)
            elif isinstance(node,ast.For):
                for value in self.expr(node.iter,scope):
                    self.assign(node.target,value,scope);self.statements(node.body,scope)
                if node.orelse: raise ValueError('For else not supported')
            elif isinstance(node,ast.Return): raise Returned(self.expr(node.value,scope) if node.value else None)
            else: raise ValueError('Unsupported static statement: '+type(node).__name__)

    def call(self,name,args=()):
        fn=self.functions[name]
        if len(fn.args.args)!=len(args):raise ValueError('Static helper arity changed')
        scope=dict(zip([a.arg for a in fn.args.args],args))
        try:self.statements(fn.body,scope)
        except Returned as result:return result.value


def registry(root):
    root=Path(root)
    native=StaticSource(source_path(root,'webui/minimax_h3_native.py'))
    h3=StaticSource(source_path(root,'webui/minimax_h3_product.py'),functions=('_seed_field',),inherited={
        name:native.name(name,{}) for name in ('FL2VA_MODEL','REF2VA_MODEL','TEXT_ENCODER','VIDEO_VAE','AUDIO_VAE','UINT64_MAX')})
    qwen=StaticSource(source_path(root,'webui/qwen_image21.py'),functions=('feature_definitions',))
    server=StaticSource(source_path(root,'webui/server.py'),functions=('_seed_field','_enrich_features'))
    features=server.name('FEATURES',{})
    h3_builders={'h3-text-video':'minimax_h3_t2v','h3-image-video':'minimax_h3_i2v','h3-reference-video':'minimax_h3_ref2va_image'}
    for item in h3.name('H3_FEATURES',{}):
        features.append({**item,'builder':h3_builders[item['id']]})
    features.extend(qwen.call('feature_definitions'))
    server.call('_enrich_features')
    return server.name('STUDIOS',{}),features,server,native


MODEL_INPUTS = {'unet_name':('diffusion model','diffusion_models'),'ckpt_name':('checkpoint','checkpoints'),
    'clip_name':('text encoder','text_encoders'),'clip_name1':('text encoder','text_encoders'),
    'clip_name2':('text encoder','text_encoders'),'vae_name':('VAE','vae'),'lora_name':('LoRA','loras'),
    'ipadapter_file':('IPAdapter','ipadapter'),'instantid_file':('InstantID adapter','instantid'),
    'control_net_name':('ControlNet','controlnet'),'swap_model':('face swap','insightface')}


def builder_models(source,symbol):
    """Read literal loader inputs from a builder AST; do not run graph builders."""
    fn=next((n for n in source.tree.body if isinstance(n,ast.FunctionDef) and n.name==symbol),None)
    if fn is None:raise ValueError('Builder symbol missing: '+symbol)
    found={}
    for node in ast.walk(fn):
        if not isinstance(node,ast.Dict):continue
        for key,value in zip(node.keys,node.values):
            if not isinstance(key,ast.Constant) or key.value not in MODEL_INPUTS:continue
            try:name=source.expr(value,{})
            except (ValueError,KeyError):continue
            if not isinstance(name,str):continue
            role,directory=MODEL_INPUTS[key.value]
            # CLIPVisionLoader uses clip_name too, but needs a separate folder.
            if 'clip_vision' in name.lower() or name.startswith('CLIP-ViT'):role,directory='vision encoder','clip_vision'
            found[name]={'name':name,'role':role,'directory_hint':directory}
    return list(found.values())



PARAMETER_KEYS = {'steps', 'cfg', 'sampler_name', 'scheduler', 'denoise', 'strength_model',
                  'width', 'height', 'batch_size', 'guidance', 'frame_rate', 'fps'}


def builder_defaults(source, symbol):
    """Extract sampler/default values from literal inputs and settings fallbacks."""
    fn = next(n for n in source.tree.body if isinstance(n, ast.FunctionDef) and n.name == symbol)
    defaults = {}
    for node in ast.walk(fn):
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            value = node.value
            key = node.targets[0].id
            fallback = None
            if isinstance(value, ast.Call) and isinstance(value.func, ast.Name) and value.func.id in {'clamp_int', 'clamp_float'} and len(value.args) > 1:
                fallback = value.args[1]
            elif isinstance(value, ast.BoolOp) and isinstance(value.op, ast.Or):
                fallback = value.values[-1]
            if fallback is not None and key in PARAMETER_KEYS | {'sampler'}:
                try: defaults['sampler_name' if key == 'sampler' else key] = source.expr(fallback, {})
                except ValueError: pass
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values):
                if isinstance(key, ast.Constant) and key.value in PARAMETER_KEYS:
                    try: literal = source.expr(value, {})
                    except (ValueError, KeyError): continue
                    if isinstance(literal, (str, int, float)) and not isinstance(literal, bool): defaults[key.value] = literal
    return defaults


def catalog(root=ROOT):
    root=Path(root)
    studios,features,server,native=registry(root)
    manifest=json.loads(source_path(root,'manifests/app_guides/catalog.json').read_text())
    mapping=manifest['features']
    builder_ast=server.constants['BUILDERS']
    builder_bindings={key.value:value.id for key,value in zip(builder_ast.keys,builder_ast.values) if isinstance(key,ast.Constant) and isinstance(value,ast.Name)}
    if set(mapping)!={f['id'] for f in features}:raise ValueError('App guide mapping and feature registry differ')
    guides=[]
    for feature in features:
        spec=mapping[feature['id']]
        route_symbol=builder_bindings.get(feature.get('builder'))
        if feature.get('builder') and not route_symbol:raise ValueError('Missing registered app builder')
        if spec['source']=='webui/server.py' and spec.get('symbol') and spec['symbol']!=route_symbol:
            raise ValueError('Guide builder does not match registered app builder')
        source_file=source_path(root,spec['source'])
        for relative in spec.get('source_files',[]): source_path(root,relative)
        source=server if spec['source']=='webui/server.py' else StaticSource(source_file)
        models=builder_models(source,spec['symbol']) if spec.get('symbol') else []
        for symbol in spec.get('related_symbols',[]):models.extend(builder_models(source,symbol))
        models_by_name={m['name']:m for m in models}
        for name in feature.get('requiredModels',[]):
            if name not in models_by_name:
                hint=spec.get('model_directories',{}).get(name,'')
                role={'diffusion_models':'diffusion model','vae':'VAE','text_encoders':'text encoder'}.get(hint,'required model')
                models_by_name[name]={'name':name,'role':role,'directory_hint':hint}
        for name,note in spec.get('model_notes',{}).items():
            if name not in models_by_name:raise ValueError('Model note does not match source loader')
            models_by_name[name]['note']=note
            models_by_name[name]['role']+=' · '+note
        templates=[{'path':spec['source']+'#'+spec['symbol'],'role':spec.get('template_role','實際程式圖建構器；不從社群 JSON 載入')}] if spec.get('symbol') else []
        for template in spec.get('templates',[]):
            source_path(root,template['path'])
            templates.append(template)
        requirements=list(spec.get('requirements',[]))
        if feature.get('requiredNodes'):requirements.append('ComfyUI 節點：'+', '.join(feature['requiredNodes']))
        if feature.get('requiredPaths'):requirements.append('模型子目錄：'+', '.join(feature['requiredPaths']))
        if feature.get('uiHidden'):requirements.append('目前一般工具選單隱藏；保留定義不代表可從選單執行。')
        if feature.get('apiEnabled') is False:requirements.append('此功能的生成 API 目前停用。')
        if feature.get('characterOnly'):requirements.append('需由已設定的角色入口使用；一般工具頁不提供完整角色資料。')
        guides.append({'id':feature['id'],'title':feature['title'],'summary':spec['summary'],
            'templates':templates,'models':list(models_by_name.values()),'steps':spec['steps'],
            'builder_symbol':route_symbol,
            'builder_defaults':{key:value for symbol in [spec.get('symbol'),*spec.get('related_symbols',[])] if symbol for key,value in builder_defaults(source,symbol).items()},
            'techniques':spec['techniques'],'requirements':requirements,
            'defaults':{f['id']:f['default'] for f in feature.get('fields',[]) if 'default' in f},
            'source_files':list(dict.fromkeys(['webui/server.py',spec['source'],*spec.get('source_files',[])])),
            'status':'source-reviewed-not-runtime-tested'})
    return {'studios':studios,'features':features,'guides':guides}


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,default=ROOT)
    args=parser.parse_args()
    print(json.dumps(catalog(args.root),ensure_ascii=False,indent=2))
