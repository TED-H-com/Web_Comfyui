"""Pure, fail-closed compilers shared by dry runs and execution adapters."""
import copy
import hashlib
import json
import math
import re
from pathlib import PurePosixPath

FEATURES = frozenset({'dance', 'video-replace', 'voice-speak', 'voxcpm-tts'})
UNITS = {'local':'local', 'colab': 'CU', 'runninghub': 'POINT', 'autoip': 'credits', 'comfy_cloud': 'credits'}
MODES = {'same_graph': '同工作流', 'same_graph_with_substitutions': '同工作流＋替換',
         'nearest_equivalent': '近似配方', 'impossible': '不支援'}
MODEL_FIELDS = {'unet_name', 'ckpt_name', 'vae_name', 'clip_name', 'lora_name'}

class ParityError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()

def fail(code, message):
    raise ParityError(code, message)

def number(value):
    return type(value) in (int, float) and math.isfinite(value) and value >= 0

def safe_relative(value):
    return (isinstance(value, str) and bool(value) and not PurePosixPath(value).is_absolute()
            and all(p not in {'', '.', '..'} for p in value.split('/'))
            and not re.search(r'[\\\x00-\x1f?#]', value))

def validate_graph(graph):
    if not isinstance(graph, dict) or not graph or len(graph) > 1000:
        fail('graph_invalid', '工作流格式不正確')
    for key, node in graph.items():
        if not isinstance(key, str) or not isinstance(node, dict) or not isinstance(node.get('class_type'), str) or not isinstance(node.get('inputs'), dict):
            fail('graph_invalid', '節點格式不正確')
        for value in node['inputs'].values():
            if isinstance(value, list) and (len(value) != 2 or value[0] not in graph or type(value[1]) is not int or value[1] < 0):
                fail('graph_link_invalid', '工作流連線不正確')
    # Reject cycles before transport, not merely dangling edges.
    seen, visiting = set(), set()
    def visit(k):
        if k in visiting: fail('graph_cycle', '工作流含循環連線')
        if k in seen: return
        visiting.add(k)
        for v in graph[k]['inputs'].values():
            if isinstance(v, list): visit(v[0])
        visiting.remove(k); seen.add(k)
    for k in graph: visit(k)
    canonical(graph)

def validate_bundle(bundle):
    if bundle.get('schema_version') != 1 or bundle.get('feature') not in FEATURES:
        fail('bundle_invalid', '未支援的基準套件')
    body = {k:v for k,v in bundle.items() if k != 'bundle_sha256'}
    if digest(body) != bundle.get('bundle_sha256') or digest(bundle['graph']) != bundle.get('graph_sha256'):
        fail('bundle_changed', '基準套件雜湊不符')
    validate_graph(bundle['graph'])
    for item in bundle['inputs'] + bundle['models']:
        if not re.fullmatch('[a-f0-9]{64}', item.get('sha256', '')) or type(item.get('bytes')) is not int or item['bytes'] < (1 if 'slot' in item else 0):
            fail('manifest_invalid', '素材或模型缺少大小與 SHA-256')
        target = item.get('name') if 'slot' in item else item.get('target')
        # A model name is a filename, its target is the install path.
        if not safe_relative(target): fail('manifest_invalid', '套件路徑不正確')
    return bundle

def voice_gate(bundle, lane, consent=False):
    if lane == 'local' or bundle['feature'] not in {'voice-speak','voxcpm-tts'}: return
    if consent is not True:
        fail('voice_consent_required', '遠端語音需要擁有者明確同意上傳聲音、參考文字與專屬權重')
    personas=[n for n in bundle['graph'].values() if n['class_type']=='CastleBreezyVoiceTTS']
    if personas:
        export=bundle.get('voice_export',{})
        if not export.get('reference_text') or not export.get('text') or not any(i['slot']=='audio' and i['name']==export.get('reference_name') for i in bundle['inputs']):
            fail('voice_reference_required','此音色尚未封裝擁有者授權的參考聲音與核准文字')
        if any(n['inputs'].get('persona')!=export.get('persona') for n in personas):
            fail('voice_reference_required','音色與擁有者封裝不一致')
        if export.get('weight_target') and not any(m.get('private') and m['target']==export['weight_target'] for m in bundle['models']):
            fail('voice_weight_required','專屬權重尚未納入不可變套件')

def lane_description(feature, lane, *, private=False):
    if feature not in FEATURES: return None
    if lane == 'autoip' and feature=='voxcpm-tts':
        mode,text='impossible','不支援：尚無 VoxCPM 聲音執行契約'
    elif lane == 'autoip':
        mode, text = 'nearest_equivalent', '平台自有模型，近似；採樣、尺寸與音色由服務決定'
    elif lane == 'comfy_cloud':
        mode, text = ('impossible', '共用環境未提供 Castle 聲音節點；須另建 Build') if feature in {'voice-speak','voxcpm-tts'} else ('same_graph_with_substitutions','原 SCAIL 圖綁定帳號素材；模型與節點版本待確認')
    elif lane == 'runninghub' and feature in {'voice-speak','voxcpm-tts'}:
        mode, text = 'impossible', '可上傳私人模型；缺相同聲音節點與專屬權重綁定契約'
    elif lane == 'runninghub':
        mode, text = 'same_graph_with_substitutions', ('私人權重可上傳收藏；BreezyVoice 執行節點尚未確認' if feature in {'voice-speak','voxcpm-tts'} else 'SCAIL 原權重；影片 loader 改上傳型，須先收藏模型')
    elif lane == 'colab' and feature in {'voice-speak','voxcpm-tts'}:
        mode, text = 'same_graph_with_substitutions', ('原 VoxCPM2／LoRA 與 speaker gate 移入自己的執行器' if feature=='voxcpm-tts' else '原 BreezyVoice 權重移入自己的執行器；需擁有者同意')
    else:
        mode, text = 'same_graph', '相同圖、權重與參數；不同 GPU 結果仍需校準'
    return {'recipe_mode':mode, 'recipe_mode_label':MODES[mode], 'recipe_difference':text,'identity_style_calibrated':False,'quality_label':'身份／畫風尚未校準'}

def schema_fields(info, values):
    """Expand core V3 dynamic-combo and autogrow fields from provider schemas."""
    fields={**info.get('input',{}).get('required',{}),**info.get('input',{}).get('optional',{})}
    for name,spec in list(fields.items()):
        if not isinstance(spec,(list,tuple)) or len(spec)<2 or not isinstance(spec[1],dict):continue
        if spec[0]=='COMFY_DYNAMICCOMBO_V3':
            selected=next((x for x in spec[1].get('options',[]) if x.get('key')==values.get(name)),None)
            if selected:
                child=selected.get('inputs',{})
                for key,value in {**child.get('required',{}),**child.get('optional',{})}.items():fields[name+'.'+key]=value
        elif spec[0]=='COMFY_AUTOGROW_V3':
            template=spec[1].get('template',{});prefix=template.get('prefix','')
            pattern=re.compile(re.escape(name+'.'+prefix)+r'(\d+)')
            for key in values:
                match=pattern.fullmatch(key)
                if match and int(match[1])<template.get('max',0):fields[key]=['DYNAMIC',{}]
    return fields

def missing_required(info, values):
    missing=set()
    def check(required, prefix=''):
        for name,spec in required.items():
            key=prefix+name
            kind=spec[0] if isinstance(spec,(list,tuple)) and spec else None
            options=spec[1] if isinstance(spec,(list,tuple)) and len(spec)>1 and isinstance(spec[1],dict) else {}
            if kind=='COMFY_AUTOGROW_V3':
                template=options.get('template',{});pattern=re.compile(re.escape(key+'.'+template.get('prefix',''))+r'(\d+)')
                indices=sorted(int(m[1]) for k in values if (m:=pattern.fullmatch(k)))
                if not template.get('min',1)<=len(indices)<=template.get('max',0) or indices!=list(range(len(indices))):missing.add(key)
                # The group is virtual: images.image0/image1 fulfill images.
                continue
            if key not in values:missing.add(key);continue
            if kind=='COMFY_DYNAMICCOMBO_V3':
                selected=next((x for x in options.get('options',[]) if x.get('key')==values[key]),None)
                if selected is None:missing.add(key)
                else:check(selected.get('inputs',{}).get('required',{}),key+'.')
            elif name not in MODEL_FIELDS | {'image','video','audio'} and isinstance(kind,list) and not isinstance(values[key],(list,dict)) and values[key] not in kind:missing.add(key)
    check(info.get('input',{}).get('required',{}))
    return missing

def dependency_probe(graph, object_info):
    """Compare model combo inputs as well as node classes; no guessing from templates."""
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
                if value not in options:
                    missing_models.add(str(value))
    ready = not (missing_nodes or missing_models or missing_inputs)
    return {'ready': ready, 'missing_nodes':sorted(missing_nodes), 'missing_models':sorted(missing_models),
            'missing_inputs':sorted(missing_inputs), 'hashes_verified':False,
            'reason': '' if ready else '初始化缺件：模型需先在你的 RunningHub 帳號收藏；缺少節點或欄位亦須先補齊'}

def compile_request(bundle, lane, *, request_id, bindings=None, voice_consent=False, voice_engine='v3'):
    validate_bundle(bundle)
    if lane not in {'local', 'colab', 'runninghub', 'autoip', 'comfy_cloud'}: fail('lane_invalid','未知平台')
    voice_gate(bundle, lane, voice_consent)
    graph = copy.deepcopy(bundle['graph']); bindings = bindings or {x['slot']:x['name'] for x in bundle['inputs']}
    changes = []
    for item in bundle['inputs']:
        value = bindings.get(item['slot'])
        if lane=='comfy_cloud' and isinstance(value,dict) and value.get('__type')=='core/ASSET':
            if set(value)!={'__type','info'} or not isinstance(value.get('info',{}).get('id'),str):fail('input_binding_invalid','素材識別碼不正確')
        elif not safe_relative(value): fail('input_binding_invalid', '素材必須綁定本帳號上傳結果')
        if item.get('node_id') is None: continue
        node = graph[item['node_id']]
        node.pop('is_changed', None)
        node['inputs'][item['field']] = value
        if node['class_type'] == 'VHS_LoadVideoPath':
            if lane in {'runninghub','comfy_cloud'}:
                node['class_type'] = 'VHS_LoadVideo'
                if lane=='runninghub':node['inputs']['force_size']='Custom'
                changes.append({'node':item['node_id'],'from':'VHS_LoadVideoPath','to':'VHS_LoadVideo','preserved':['force_rate','frame_load_cap','custom_width','custom_height','skip_first_frames','select_every_nth','audio']})
            elif lane == 'colab':
                node['inputs']['video'] = '/content/castle-parity-input/' + value
    for node in graph.values():
        if lane == 'runninghub' and node['inputs'].get('lora_name') == 'Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64.safetensors':
            # Official public page 1945379281314750465 matches local MD5;
            # the unsuffixed public model has a DIFFERENT hash and is rejected.
            node['inputs']['lora_name'] = 'Wan21_I2V_14B_lightx2v_cfg_step_distill_lora_rank64_official.safetensors'
            changes.append({'model':'lightx2v','page_id':'1945379281314750465','verification':'public_page_md5'})
            if node['class_type']=='LoraLoaderModelOnly':
                clip=next((key for key,value in graph.items() if value['class_type']=='CLIPLoader'),None)
                if clip is None:fail('model_binding_missing','缺少原工作流的文字編碼器')
                node['class_type']='LoraLoader'
                node['inputs'].update(clip=[clip,0],strength_clip=0.0)
                changes.append({'from':'LoraLoaderModelOnly','to':'LoraLoader','strength_clip':0.0,
                    'reason':'official favourite exposed only on LoraLoader; MODEL output and strength unchanged'})
        if lane!='local'  and node['class_type'] in {'CastleBreezyVoiceTTS','CastleBreezyVoiceClone'}:
            node['inputs'].pop('daemon_url',None)
            if lane=='colab':changes.append({'node_class':node['class_type'],'from':'local daemon','to':'isolated owner runtime','weights':'unchanged'})
        node.pop('is_changed', None)
        if 'filename_prefix' in node['inputs']:
            node['inputs']['filename_prefix'] = 'parity/' + request_id
    validate_graph(graph)
    result = {'lane':lane,'bundle_sha256':bundle['bundle_sha256'], 'substitutions':changes,
              **lane_description(bundle['feature'],lane,private=bundle.get('private_persona',False))}
    if lane == 'autoip':
        if bundle['feature'] == 'voice-speak':
            node = next((n for n in graph.values() if n['class_type']=='CastleBreezyVoiceClone'), None)
            if node is None:
                export=bundle.get('voice_export',{})
                if not export.get('reference_text') or 'audio' not in bindings: fail('voice_reference_required','此音色尚未封裝擁有者授權的參考聲音')
                node={'inputs':{'text':export['text'],'ref_text':export['reference_text']}}
            if voice_engine not in {'v1','v1-tw','v2','v3'}: fail('voice_engine_invalid','無效語音引擎')
            if not 1<=len(node['inputs']['text'])<=1200 or len(node['inputs'].get('ref_text',''))>500:fail('voice_text_invalid','語音文字超過此平台契約長度；不會截斷或改寫')
            fields = {'text':node['inputs']['text'], 'voice_engine':voice_engine}
            if voice_engine=='v3':fields.update(reference_text=node['inputs']['ref_text'], dialect_rewrite='0')
            files = {'reference_audio':bindings['audio']}; path='/api/v1/voice/clone'
            omitted = ['BreezyVoice weights', 'Taiwan accent equivalence']
        else:
            prompt = bundle['material_settings'].get('positive') or bundle.get('effective_prompt','')
            if len(prompt)>500:fail('prompt_too_long','AutoIP 描述上限 500 字；不可自動截斷')
            fields={'mode':'animation' if bundle['feature']=='dance' else 'replacement', 'prompt':prompt,
                    'character_count':'1','aspect_ratio':'9:16'}
            files={'image':bindings['reference'],'reference_video':bindings['video']};path='/api/v1/videos/character-dance'
            omitted=['SCAIL weights','seed','negative','steps','cfg','exact dimensions','exact frame count','sampler']
        result.update(method='POST',path=path,encoding='multipart/form-data',fields=fields,files=files,
                      differences=omitted,quote_requests=[{'method':'GET','path':'/api/v1/pricing'},{'method':'GET','path':'/api/v1/credits/balance'}])
    elif lane == 'comfy_cloud':
        result.update(method='POST',path='/api/v2/jobs',json={'workflow':graph},headers={'Idempotency-Key':request_id},probe_graph=graph,submit_allowed=bundle['feature'] not in {'voice-speak','voxcpm-tts'})
    else:
        result.update(method='POST',path='/prompt',json={'prompt':graph,'client_id':request_id,
            'extra_data':{'castle_request_id':request_id,'bundle_sha256':bundle['bundle_sha256']}})
        if lane=='colab':result.update(gpu='A100',cache='/content/castle-parity-cache/'+bundle['bundle_sha256'])
        if lane=='runninghub':result.update(proxy_kind='proxy-plus',capacity_gb=48)
    return result
