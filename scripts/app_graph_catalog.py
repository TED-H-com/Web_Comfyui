"""Read-only diagrams of app builders. Output is documentation, never prompt JSON.

Reads only allowlisted Python/JS source. No source import, eval, exec, network,
environment, model or user-data access. Dynamic graph branches are explicit
representative variants; process diagrams are source-reviewed explanations.
"""
from __future__ import annotations

import ast
import math
from pathlib import Path
import re

SOURCE_FILES = ('webui/server.py', 'webui/minimax_h3_native.py',
                'webui/qwen_image21.py', 'webui/static/app.js')
UNKNOWN = object()
MODEL_FIELDS = frozenset(('unet_name','ckpt_name','clip_name','clip_name1','clip_name2',
    'vae_name','lora_name','ipadapter_file','instantid_file','control_net_name','swap_model','face_restore_model'))
NUMBER_FIELDS = frozenset(('width','height','steps','cfg','denoise','guidance','strength_model',
    'weight','shift','batch_size','length','fps','frame_rate','force_rate','pose_strength',
    'context_length','context_overlap','start_at','end_at','ip_weight','cn_strength','noise',
    'face_restore_visibility','codeformer_weight','resize_type.megapixels','resize_type.multiple'))
ENUM_FIELDS = frozenset(('sampler_name','scheduler','weight_dtype','type','crop','format',
    'resize_type','scale_method','weight_type','combine_embeds','embeds_scaling','provider'))
ENUM_VALUES = frozenset(('default','flux','lumina2','wan','qwen_image','minimax','res_multistep','simple','euler','karras','dpmpp_2m','none','center','mp4','video/h264-mp4','scale total pixels','scale to multiple','nearest-exact','linear','concat','V only','CPU','CUDA','average'))
SECRET = re.compile(r'(?:sk-|aip_|gh[pousr]_|hf_)[A-Za-z0-9_-]{12,}', re.I)


def source_path(root, relative):
    root = Path(root).resolve()
    if relative not in SOURCE_FILES: raise ValueError('Undeclared graph source')
    path = root / relative
    if not path.resolve().is_relative_to(root) or any(p.is_symlink() for p in (path,*path.parents) if p != root and p.is_relative_to(root)):
        raise ValueError('Graph source must not be a symlink')
    if not path.is_file(): raise ValueError('Missing graph source: '+relative)
    return path


class Source:
    def __init__(self, path):
        self.tree = ast.parse(path.read_text(encoding='utf-8'))
        self.functions = {n.name:n for n in ast.walk(self.tree) if isinstance(n,ast.FunctionDef)}
        self.constants = {}
        for n in self.tree.body:
            if isinstance(n,(ast.Assign,ast.AnnAssign)):
                target=n.targets[0] if isinstance(n,ast.Assign) else n.target
                if isinstance(target,ast.Name): self.constants[target.id]=n.value

    def function(self, name):
        if name not in self.functions: raise ValueError('Graph source function changed: '+name)
        return self.functions[name]

    def value(self, node, scope=None, seen=()):
        """Only literal containers, names and simple reference-ID arithmetic."""
        scope=scope or {}
        v=lambda n:self.value(n,scope,seen)
        if isinstance(node,ast.Constant): return node.value
        if isinstance(node,ast.Name):
            if node.id in scope:return scope[node.id]
            if node.id in self.constants and node.id not in seen:
                return self.value(self.constants[node.id],scope,(*seen,node.id))
            return UNKNOWN
        if isinstance(node,(ast.List,ast.Tuple)):
            values=[v(n) for n in node.elts]
            return UNKNOWN if any(x is UNKNOWN for x in values) else values
        if isinstance(node,ast.Dict):
            out={}
            for k,n in zip(node.keys,node.values):
                key=v(k) if k else UNKNOWN
                if isinstance(key,str):out[key]=v(n)
            return out
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):
            x=v(node.operand)
            return -x if isinstance(x,(int,float)) else UNKNOWN
        if isinstance(node,ast.BinOp) and isinstance(node.op,ast.Add):
            left,right=v(node.left),v(node.right)
            if type(left) is type(right) and type(left) in (str,int): return left+right
        if isinstance(node,ast.JoinedStr):
            values=[v(n) for n in node.values]
            return ''.join(str(x) for x in values) if all(x is not UNKNOWN for x in values) else UNKNOWN
        if isinstance(node,ast.FormattedValue):return v(node.value)
        if isinstance(node,ast.IfExp):
            test=v(node.test)
            if type(test) is bool:return v(node.body if test else node.orelse)
        # Every call/attribute/subscript is deliberately unresolved. In
        # particular even a literal open()/getenv() must never be executed.
        return UNKNOWN

    def scope(self, fn):
        out={}
        for stmt in fn.body:
            if isinstance(stmt,ast.Assign) and len(stmt.targets)==1 and isinstance(stmt.targets[0],ast.Name):
                value=self.value(stmt.value,out)
                if value is not UNKNOWN:out[stmt.targets[0].id]=value
        return out


def graph_literal(node):
    return (isinstance(node,ast.Dict) and bool(node.keys) and all(
        isinstance(k,ast.Constant) and isinstance(k.value,str) and isinstance(v,ast.Dict)
        and any(isinstance(key,ast.Constant) and key.value=='class_type' for key in v.keys)
        for k,v in zip(node.keys,node.values)))


def primary_graph(source, symbol, *, largest=False, scope=None):
    fn=source.function(symbol)
    local=source.scope(fn);local.update(scope or {})
    candidates=[]
    for statement in fn.body:
        n=statement.value if isinstance(statement,(ast.Assign,ast.Return)) else None
        if graph_literal(n):candidates.append(n)
    if largest or not candidates:
        candidates=[n for n in ast.walk(fn) if graph_literal(n)]
    if not candidates:raise ValueError('No literal graph in '+symbol)
    graph=max(candidates,key=lambda n:len(n.keys)) if largest else candidates[-1]
    return source.value(graph,local),fn,local


def fixed_additions(source, statements, graph, scope):
    for stmt in statements:
        if not isinstance(stmt,ast.Assign) or len(stmt.targets)!=1:continue
        target=stmt.targets[0]
        if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Name) and target.value.id in {'graph','prompt'}:
            node_id=source.value(target.slice,scope)
            value=source.value(stmt.value,scope)
            if isinstance(node_id,str) and isinstance(value,dict) and isinstance(value.get('class_type'),str):graph[node_id]=value


def model_value(value):
    if not isinstance(value,str) or len(value)>240 or SECRET.search(value):return False
    normalized=value.replace('\\','/')
    return bool(re.fullmatch(r'[\w .()+/-]+\.(?:safetensors|ckpt|gguf|pth|pt|bin|onnx|sft)',normalized,re.I)
                and not normalized.startswith(('/','~')) and '..' not in normalized.split('/'))


def diagram(raw, source, notes=()):
    nodes=[];links=[];ids=set(raw)
    omitted=0
    for id, node in raw.items():
        kind=node.get('class_type')
        if not isinstance(kind,str) or SECRET.search(kind) or not re.fullmatch(r'[A-Za-z0-9_+ .:-]{1,100}',kind) or not re.fullmatch(r'[0-9]{1,8}',id):
            raise ValueError('Unresolved graph node type')
        inputs={}
        for key,value in node.get('inputs',{}).items():
            if SECRET.search(key) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,90}',key):continue
            if isinstance(value,list) and len(value)==2 and isinstance(value[0],str) and type(value[1]) is int:
                if value[0] in ids:links.append({'from':value[0],'to':id,'from_slot':value[1],'to_slot':key})
                else:omitted+=1
            elif key in MODEL_FIELDS and model_value(value):inputs[key]=value
            elif key in NUMBER_FIELDS and type(value) in (int,float) and math.isfinite(value) and abs(value)<=10_000_000:inputs[key]=value
            elif key in ENUM_FIELDS and isinstance(value,str) and value in ENUM_VALUES:inputs[key]=value
        nodes.append({'id':id,'type':kind,'inputs':inputs})
    if omitted:notes=(*notes,f'{omitted} 條無法在此代表分支解析的連線未顯示。')
    return {'kind':'comfyui','evidence':'從實際 Python builder 的 AST 讀取節點與插槽連線；已遮蔽提示詞、輸入媒體與執行期值。僅供閱讀，不能當作可執行 prompt JSON。',
            'source':source,'nodes':nodes,'links':links,'notes':list(notes)}


def ip_blend(source):
    symbol='ip_blend_prompt'
    raw,fn,scope=primary_graph(source,symbol)
    loop=next(n for n in fn.body if isinstance(n,ast.For))
    model_ref=['1',0]
    for i in range(2):
        scope.update(load_id=str(10+2*i),adapter_id=str(11+2*i),model_ref=model_ref)
        fixed_additions(source,loop.body,raw,scope)
        model_ref=[scope['adapter_id'],0]
    scope['model_ref']=model_ref
    fixed_additions(source,fn.body,raw,scope)
    return diagram(raw,'webui/server.py#'+symbol,['代表分支：兩張參考圖，依實際迴圈建立兩個 IPAdapterAdvanced；第三張會再串接一組。'])


def qwen(source,edit):
    raw,fn,scope=primary_graph(source,'compile_prompt')
    scope.update(edit=edit,node_id='11',i=1)
    branch=next(n for n in fn.body if isinstance(n,ast.If) and isinstance(n.test,ast.Name) and n.test.id=='edit')
    statements=branch.body if edit else branch.orelse
    fixed_additions(source,statements,raw,scope)
    if edit:
        loop=next(n for n in statements if isinstance(n,ast.For))
        fixed_additions(source,loop.body,raw,scope)
        # Read actual input assignment and .update(...) literal kwargs; no calls.
        for stmt in loop.body:
            if isinstance(stmt,ast.Assign) and isinstance(stmt.targets[0],ast.Subscript):
                target=stmt.targets[0]
                if isinstance(target.value,ast.Subscript) and isinstance(target.value.value,ast.Subscript):
                    root=target.value.value
                    if isinstance(root.value,ast.Name) and root.value.id=='graph':
                        id=source.value(root.slice,scope);key=source.value(target.slice,scope)
                        if id in raw and isinstance(key,str):raw[id]['inputs'][key]=source.value(stmt.value,scope)
        for stmt in statements:
            if isinstance(stmt,ast.Expr) and isinstance(stmt.value,ast.Call) and isinstance(stmt.value.func,ast.Attribute) and stmt.value.func.attr=='update':
                target=stmt.value.func.value
                if isinstance(target,ast.Subscript) and isinstance(target.value,ast.Subscript):
                    id=source.value(target.value.slice,scope)
                    if id in raw:
                        for keyword in stmt.value.keywords:raw[id]['inputs'][keyword.arg]=source.value(keyword.value,scope)
    return diagram(raw,'webui/qwen_image21.py#compile_prompt',[
        '代表分支：一張輸入圖片；實際改圖支援依序加入最多四張。' if edit else '文生圖分支：ResolutionSelector 決定空白 latent 尺寸。',
        '這是應用程式 builder 的連線，並非官方參考 JSON 的逐字複本。'])


def h3(source,id):
    symbol={'h3-text-video':'build_t2v_prompt','h3-image-video':'build_i2v_prompt','h3-reference-video':'build_ref2va_prompt'}[id]
    fn=source.function(symbol)
    call=next(n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_build_graph')
    scope={}
    for stmt in fn.body:
        if isinstance(stmt,ast.AnnAssign) and isinstance(stmt.target,ast.Name) and stmt.target.id=='conditioning_inputs':scope['conditioning_inputs']=source.value(stmt.value)
    for keyword in call.keywords:
        value=source.value(keyword.value,scope)
        if value is not UNKNOWN:scope[keyword.arg]=value
    raw,_,_=primary_graph(source,'_build_graph',scope=scope)
    conditioning=raw['5']['inputs']
    if id=='h3-image-video':
        branch=next(n for n in fn.body if isinstance(n,ast.If) and ast.unparse(n.test)=='clean_first is not None' and any(isinstance(x,ast.Assign) and isinstance(x.targets[0],ast.Subscript) for x in n.body))
        local={'node_id':'15'};fixed_additions(source,branch.body,raw,local)
        for stmt in branch.body:
            if isinstance(stmt,ast.Assign) and isinstance(stmt.targets[0],ast.Subscript) and isinstance(stmt.targets[0].value,ast.Name) and stmt.targets[0].value.id=='conditioning_inputs':
                conditioning[source.value(stmt.targets[0].slice,local)]=source.value(stmt.value,local)
    elif id=='h3-reference-video':
        loops=[n for n in fn.body if isinstance(n,ast.For)]
        for loop,node_id in zip(loops,['15','30','40']):
            local={'index':0,'node_id':node_id};fixed_additions(source,loop.body,raw,local)
            for stmt in loop.body:
                if isinstance(stmt,ast.Assign) and isinstance(stmt.targets[0],ast.Subscript) and isinstance(stmt.targets[0].value,ast.Name) and stmt.targets[0].value.id=='conditioning_inputs':
                    conditioning[source.value(stmt.targets[0].slice,local)]=source.value(stmt.value,local)
    notes=['Stock 代表分支；Sage／EasyCache／TeaCache 的可選包裝不在此圖。']
    if id=='h3-image-video':notes.append('顯示首格一張的分支；尾格或首尾兩張會加入相應 LoadImage。')
    if id=='h3-reference-video':notes.append('顯示一圖、一影片、一音訊；影片的影像與音訊分別連入條件節點，實際支援更多參考槽。')
    return diagram(raw,'webui/minimax_h3_native.py#'+symbol,notes)


def process(source,symbol,rows,edges,notes=(),required=()):
    source.function(symbol)
    for name in required:source.function(name)
    nodes=[{'id':id,'type':kind,'title':title} for id,kind,title in rows]
    ids={n['id'] for n in nodes}
    if any(a not in ids or b not in ids for a,b in edges):raise ValueError('Invalid process endpoint')
    return {'kind':'process','evidence':'依實際 handler／helper 整理的程序示意，非 ComfyUI 節點圖，也不是可執行 JSON；未執行任何服務。',
            'source':'webui/server.py#'+symbol,'nodes':nodes,'links':[{'from':a,'to':b} for a,b in edges],'notes':list(notes)}


def process_graphs(server,js):
    out={}
    out['voice-speak']=process(server,'voice_speak_prompt',[
        ('input','input','文字與已設定的聲音'),('route','branch','角色聲音或聲音庫克隆'),
        ('persona','CastleBreezyVoiceTTS','角色聲音分段生成'),('clone','voice_clone_prompt','聲音庫 F5 / BreezyVoice 克隆'),
        ('concat','add_audio_concat_and_save','合併音訊並儲存')],
        [('input','route'),('route','persona'),('route','clone'),('persona','concat'),('clone','concat')],
        ['角色／聲音資料須由本機提供；流程會依入口與選項分支。'],required=('voice_clone_prompt','add_audio_concat_and_save'))
    out['voice-clone']=process(server,'voice_clone_prompt',[
        ('input','LoadAudio','參考音訊與逐字稿'),('chunks','prepare_voice_script_chunks','切分要說的文字'),
        ('f5','F5TTSAudioInputs','F5 語系模型分支'),('breezy','CastleBreezyVoiceClone','台灣腔服務分支'),
        ('output','add_audio_concat_and_save','串接各段並儲存')],
        [('input','f5'),('input','breezy'),('chunks','f5'),('chunks','breezy'),('f5','output'),('breezy','output')],
        ['分支二擇一；此圖省略可變段數的重複節點與本機參考資料。'],required=('prepare_voice_script_chunks','add_audio_concat_and_save'))
    out['voxcpm-tts']=process(server,'start_voxcpm_voice_job',[
        ('input','input','文字、模式與本機 profile'),('queue','start_voxcpm_voice_job','建立本機排程工作'),
        ('worker','run_voxcpm_voice_job','執行語音工作程序'),('result','output','回報音訊或工作錯誤')],
        [('input','queue'),('queue','worker'),('worker','result')],
        ['顯示 VoxCPM 分支；BreezyVoice 是另一條服務路徑。voxcpm_direct_prompt 明確拒絕作為 ComfyUI graph。'],required=('run_voxcpm_voice_job','voxcpm_direct_prompt'))
    out['voice-batch']=process(server,'api_voiceover_create_batch',[
        ('text','input','長文段落'),('pptx','api_voiceover_extract_pptx','PPTX 備忘稿擷取'),
        ('batch','api_voiceover_create_batch','管理員建立配音批次'),('inspect','api_voiceover_batch','讀取各段狀態'),
        ('redo','api_voiceover_redo','選段重作'),('zip','api_voiceover_zip','取得批次音訊')],
        [('text','batch'),('pptx','batch'),('batch','inspect'),('inspect','redo'),('redo','inspect'),('inspect','zip')],
        ['操作流程，不代表單一 ComfyUI 工作流；需已配置的語音工作程序。'],required=('api_voiceover_extract_pptx','api_voiceover_batch','api_voiceover_redo','api_voiceover_zip'))
    out['voice-library']=process(server,'api_voices_upload',[
        ('upload','api_voices_upload','上傳參考音訊'),('history','api_voices_import_history','從已有作品匯入'),
        ('index','save_voice_index','儲存聲音庫索引'),('list','api_voices_list','顯示可見聲音'),
        ('file','api_voices_file','讀取授權音訊')],
        [('upload','index'),('history','index'),('index','list'),('list','file')],
        ['素材管理，不執行語音生成；不含任何使用者音檔。'],required=('api_voices_import_history','save_voice_index','api_voices_list','api_voices_file'))
    out['post-generator']=process(server,'generate_post',[
        ('input','input','主題、平台、語氣與長度'),('prompt','_post_task_prompt','組合文字任務'),
        ('claude','_post_via_claude_cli','Claude / Grok 風格本機 CLI'),('openai','_post_via_openai','使用者設定的 OpenAI API'),
        ('gemini','_post_via_gemini','使用者設定的 Gemini API'),('result','output','校對生成文案')],
        [('input','prompt'),('prompt','claude'),('prompt','openai'),('prompt','gemini'),('claude','result'),('openai','result'),('gemini','result')],
        ['後端依選項擇一；Grok 為風格模擬。此圖不含端點、金鑰或真實請求。'],required=('_post_task_prompt','_post_via_claude_cli','_post_via_openai','_post_via_gemini'))
    if not re.search(r'async function createExperimentalCharacter\(form\)',js) or '"/api/characters/experimental"' not in js:
        raise ValueError('Character creation frontend changed')
    out['character-create']=process(server,'api_character_experimental_create',[
        ('form','createExperimentalCharacter','輸入名稱與 1–6 張圖片'),('browser','validation','瀏覽器檢查格式與大小'),
        ('auth','authentication','POST 驗證登入與角色數量'),('decode','decode_data_url','解碼上傳資料'),
        ('sniff','sniff_experimental_image','核對實際圖片格式'),('quota','experimental_total_bytes_for_user','核對個人空間配額'),
        ('assets','storage','儲存私有角色圖片'),('character','upsert_character','建立 private / visualClass unknown 角色'),
        ('public','character_public_for_user','依使用者權限回傳資料'),('workspace','navigation','前端重新載入並打開角色工作區')],
        [('form','browser'),('browser','auth'),('auth','decode'),('decode','sniff'),('sniff','quota'),('quota','assets'),('assets','character'),('character','public'),('public','workspace')],
        ['另見 webui/static/app.js#createExperimentalCharacter。','建立角色只是私有素材與索引寫入，不會訓練 LoRA、驗證身份或生成圖片。','未顯示實際姓名、檔名、儲存路徑、帳號或上傳內容。'],required=('sniff_experimental_image','experimental_total_bytes_for_user','upsert_character','character_public_for_user'))
    return out


def graphs(root,app_catalog):
    """Return ID -> read-only graph for every actual app feature + character-create."""
    server=Source(source_path(root,'webui/server.py'))
    native=Source(source_path(root,'webui/minimax_h3_native.py'))
    qwen_source=Source(source_path(root,'webui/qwen_image21.py'))
    result=process_graphs(server,source_path(root,'webui/static/app.js').read_text())
    guides={g['id']:g for g in app_catalog['guides']}
    for feature in app_catalog['features']:
        id=feature['id']
        if id in result:continue
        if id.startswith('h3-'):result[id]=h3(native,id);continue
        if id in {'qwen21-edit','qwen21-text2img'}:result[id]=qwen(qwen_source,id=='qwen21-edit');continue
        if id=='image-blend':result[id]=ip_blend(server);continue
        symbol=guides[id].get('builder_symbol')
        raw,_,_=primary_graph(server,symbol,largest=id=='clipmode-character-photoshoot',scope={'model_ref':['1',0]} if id=='text2img' else None)
        notes=[]
        if id=='text2img':notes=['預設 useDopsd=false；啟用時才插入 DOPSD LoRA，這裡顯示無 LoRA 的預設連線。']
        if id=='dance':notes=['顯示目前表單的 animate 分支；不混入隱藏 replace／分段流程。']
        if id=='clipmode-character-photoshoot':notes=['代表分支：真人角色 InstantID + ReActor；動漫角色改用角色 LoRA，未合併兩種互斥分支。','身份輸入由伺服器綁定；圖中不公開任何角色素材。']
        if feature.get('apiEnabled') is False:notes.append('此入口的生成 API 目前停用，圖表不代表可執行。')
        result[id]=diagram(raw,'webui/server.py#'+symbol,notes)
    expected={f['id'] for f in app_catalog['features']}|{'character-create'}
    if set(result)!=expected:raise ValueError('Graph coverage differs from app registry')
    return english_labels(result)


ENGLISH_NOTES = {
 '代表分支：兩張參考圖，依實際迴圈建立兩個 IPAdapterAdvanced；第三張會再串接一組。': 'Representative branch: two references create two chained IPAdapterAdvanced nodes using the actual loop. A third reference adds another pair.',
 '代表分支：一張輸入圖片；實際改圖支援依序加入最多四張。': 'Representative branch: one input image. Editing supports up to four images in sequence.',
 '文生圖分支：ResolutionSelector 決定空白 latent 尺寸。': 'Text-to-image branch: ResolutionSelector determines the empty latent dimensions.',
 '這是應用程式 builder 的連線，並非官方參考 JSON 的逐字複本。': 'These are application builder connections, not a verbatim copy of the official reference JSON.',
 'Stock 代表分支；Sage／EasyCache／TeaCache 的可選包裝不在此圖。': 'Representative Stock branch. Optional Sage, EasyCache and TeaCache wrappers are not shown.',
 '顯示首格一張的分支；尾格或首尾兩張會加入相應 LoadImage。': 'Shows one first-frame image. A last frame or both frames adds the corresponding LoadImage nodes.',
 '顯示一圖、一影片、一音訊；影片的影像與音訊分別連入條件節點，實際支援更多參考槽。': 'Shows one image, one video and one audio reference. Video frames and audio connect separately to conditioning; additional reference slots are supported.',
 '角色／聲音資料須由本機提供；流程會依入口與選項分支。': 'Character and voice data must be supplied locally. The route depends on the entry point and options.',
 '分支二擇一；此圖省略可變段數的重複節點與本機參考資料。': 'Choose one branch. Repeated nodes for variable text chunks and local reference data are omitted.',
 '顯示 VoxCPM 分支；BreezyVoice 是另一條服務路徑。voxcpm_direct_prompt 明確拒絕作為 ComfyUI graph。': 'Shows the VoxCPM branch; BreezyVoice uses a separate service route. voxcpm_direct_prompt explicitly rejects use as a ComfyUI graph.',
 '操作流程，不代表單一 ComfyUI 工作流；需已配置的語音工作程序。': 'An operational process, not a single ComfyUI workflow. A configured voice worker is required.',
 '素材管理，不執行語音生成；不含任何使用者音檔。': 'Asset management only; it does not generate speech or include user audio.',
 '後端依選項擇一；Grok 為風格模擬。此圖不含端點、金鑰或真實請求。': 'The selected option chooses one backend. Grok is a style simulation. No endpoints, credentials or live requests are included.',
 '另見 webui/static/app.js#createExperimentalCharacter。': 'Also see webui/static/app.js#createExperimentalCharacter.',
 '建立角色只是私有素材與索引寫入，不會訓練 LoRA、驗證身份或生成圖片。': 'Character creation stores private assets and an index. It does not train a LoRA, verify identity or generate images.',
 '未顯示實際姓名、檔名、儲存路徑、帳號或上傳內容。': 'Real names, filenames, storage paths, accounts and upload content are omitted.',
 '預設 useDopsd=false；啟用時才插入 DOPSD LoRA，這裡顯示無 LoRA 的預設連線。': 'Default useDopsd=false. Enabling it inserts the DOPSD LoRA; this view shows the default connections without that LoRA.',
 '顯示目前表單的 animate 分支；不混入隱藏 replace／分段流程。': 'Shows the current animate form branch, without the hidden replace or segmented routes.',
 '代表分支：真人角色 InstantID + ReActor；動漫角色改用角色 LoRA，未合併兩種互斥分支。': 'Representative branch: InstantID + ReActor for a realistic character. Anime characters use a character LoRA; the mutually exclusive branches are not combined.',
 '身份輸入由伺服器綁定；圖中不公開任何角色素材。': 'The server binds identity inputs. No character assets are exposed in this diagram.',
 '此入口的生成 API 目前停用，圖表不代表可執行。': 'Generation is disabled for this entry point. The diagram does not mean it can be run.',
}
PROCESS_TITLES_EN = {
 'voice-speak': ['Text and configured voice','Choose character voice or library cloning','Generate character speech in chunks','F5 / BreezyVoice library cloning','Concatenate and save audio'],
 'voice-clone': ['Reference audio and transcript','Split the target speech text','F5 language-model branch','Taiwanese Mandarin service branch','Concatenate chunks and save'],
 'voxcpm-tts': ['Text, mode and local profile','Create a locally queued job','Run the voice worker','Return audio or a job error'],
 'voice-batch': ['Text sections','Extract PPTX speaker notes','Administrator creates a voiceover batch','Read segment status','Regenerate selected segments','Download batch audio'],
 'voice-library': ['Upload reference audio','Import an existing output','Save the voice-library index','List accessible voices','Read authorized audio'],
 'post-generator': ['Topic, platform, tone and length','Build the text task','Local CLI for Claude / Grok style','User-configured OpenAI API','User-configured Gemini API','Review the generated post'],
 'character-create': ['Enter a name and 1–6 images','Check format and size in the browser','Authenticate POST and check character count','Decode uploaded data','Verify the actual image format','Check personal storage quota','Store private character images','Create a private character with unknown visual class','Return data allowed for this user','Reload and open the character workspace'],
}


def english_labels(result):
    for id,graph in result.items():
        graph['evidence_en'] = (
            'Nodes and slot connections read from the actual Python builder AST. Prompts, input media and runtime values are omitted. For inspection only; this is not executable prompt JSON.'
            if graph['kind']=='comfyui' else
            'Process explanation reviewed against the actual handlers and helpers. This is neither a ComfyUI node graph nor executable JSON. No service was run.')
        graph['notes_en'] = [ENGLISH_NOTES.get(note, 'An unresolved connection is omitted from this representative branch.') for note in graph.get('notes',[])]
        if graph['kind']=='process':
            titles=PROCESS_TITLES_EN[id]
            if len(titles)!=len(graph['nodes']):raise ValueError('English process titles need review')
            for node,title in zip(graph['nodes'],titles):node['title_en']=title
    return result
