"""Colab admission vocabulary and strict byte-derived transport names."""
from pathlib import Path
ADMISSION_MESSAGES={'account_busy':'帳號正在配置 GPU，請稍候幾秒再送出','unsupported_input_format': '不支援的參考檔案格式', 'input_too_large': '參考檔案合計上限為 32 MB', 'invalid_input': '無效的參考檔案', 'invalid_manifest': '生成設定或參考素材不符合這個模式；請檢查秒數、素材種類及描述中的參考編號', 'invalid_media_image': '下載成果不是有效的圖片', 'invalid_media_stream': '下載成果沒有有效的影音串流', 'account_unavailable': '請重新連接 Google 帳號以確認身分，再開始生成', 'insufficient_balance': '請先連接帳號並確認點數餘額足夠；點數上限不能高於目前餘額', 'lane_busy': '這個帳號已有執行中或待回收的任務，請先完成或釋放 GPU', 'input_suffix_mismatch': '參考檔案副檔名與內容不符', 'admission_unavailable': 'Colab 入列前檢查未完成；請確認素材與帳號後建立新請求', 'request_conflict': '同一請求識別碼不能用於不同內容', 'unsupported_gpu': '不支援這個 GPU 選項', 'invalid_limits': '點數及時間上限必須是數字', 'limits_out_of_range': '點數上限須為 5–2000；執行時間須為 5–180 分鐘', 'missing_spec': '缺少生成設定', 'unsupported_spec': '不支援的生成設定', 'media_tool_missing': '請先安裝 ffmpeg／ffprobe，才能驗證生成的影音成果', 'too_many_inputs': '最多提供 8 個參考檔案', 'extend_input_required': '延長影片需要一段來源影片', 'frame_inputs_required': '首尾幀模式需要 1–2 張圖片', 'session_account_busy': '既有工作階段帳號不符或關閉中，請先確認釋放', 'session_busy': '這個帳號已有工作階段；請先關閉並等候釋放', 'runner_source_invalid': '執行器來源不能是符號連結', 'account_not_found': '找不到這個帳號', 'invalid_identity': '無效的任務或帳號識別碼'}
MIMES={'.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp','.mp4':'video/mp4','.mov':'video/quicktime','.webm':'video/webm','.wav':'audio/wav','.mp3':'audio/mpeg','.flac':'audio/flac','.m4a':'audio/mp4'}
def input_name(name,raw,kind):
    from colab_service import ColabError
    from provider_fabric.media_signatures import classify_declared_media,SIGNATURE_PROBE_BYTES
    suffix=Path(name).suffix.lower();probe=raw[:SIGNATURE_PROBE_BYTES]
    if suffix in MIMES:
        if not MIMES[suffix].startswith(kind+'/') or not classify_declared_media(MIMES[suffix],probe):
            raise ColabError(ADMISSION_MESSAGES['input_suffix_mismatch'])
        return name
    for ext,mime in MIMES.items():
        if mime.startswith(kind+'/') and classify_declared_media(mime,probe):return name+ext
    raise ColabError(ADMISSION_MESSAGES['unsupported_input_format'])

def preflight(bundle,files,root):
    # Same size, signature and full decoder as submit; no account/provider calls.
    import tempfile
    from colab_service import MAX_INPUT,ColabError,private_write,validate_media
    if len(files)>8:raise ColabError(ADMISSION_MESSAGES['too_many_inputs'])
    if any(not v for v in files.values()) or sum(map(len,files.values()))>MAX_INPUT:raise ColabError(ADMISSION_MESSAGES['input_too_large'])
    with tempfile.TemporaryDirectory(prefix='input-preview-',dir=root) as directory:
        for item in bundle['inputs']:
            raw=files[item['slot']];name=input_name(item['name'],raw,item['kind'])
            p=Path(directory)/Path(name).name;private_write(p,raw);validate_media(p)
