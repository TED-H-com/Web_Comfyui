import sys
from pathlib import Path
import json

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'webui'))

import server

def test_comfy_error_payload_validation_required_input_missing():
    raw_error = json.dumps({
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation"
        },
        "node_errors": {
            "node_1": {
                "errors": [
                    {
                        "type": "required_input_missing",
                        "extra_info": {"input_name": "image"},
                        "details": "image"
                    }
                ]
            }
        }
    })
    payload = server.comfy_error_payload(raw_error)
    assert payload is not None
    assert payload["error_code"] == "comfy_prompt_validation"
    assert "缺少目前節點要求的內部設定" in payload["message"]
    assert "image" in payload["technical_detail"]
    assert "required_input_missing" in payload["technical_detail"]

def test_comfy_error_payload_validation_incompatible_schema():
    raw_error = json.dumps({
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation"
        },
        "node_errors": {
            "node_2": {
                "errors": [
                    {
                        "type": "invalid_schema",
                        "extra_info": {"input_name": "latent"},
                        "details": "latent"
                    }
                ]
            }
        }
    })
    payload = server.comfy_error_payload(raw_error)
    assert payload is not None
    assert payload["error_code"] == "comfy_prompt_validation"
    assert "與目前節點版本不相容" in payload["message"]
    assert "latent" in payload["technical_detail"]

def test_comfy_error_payload_validation_general_error():
    raw_error = json.dumps({
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation"
        }
    })
    payload = server.comfy_error_payload(raw_error)
    assert payload is not None
    assert payload["error_code"] == "comfy_prompt_validation"
    assert "與目前節點版本不相容" in payload["message"]

def test_comfy_error_payload_validation_not_validation():
    raw_error = json.dumps({
        "error": {
            "type": "some_other_error",
            "message": "Some other error"
        }
    })
    payload = server.comfy_error_payload(raw_error)
    assert payload is None

def test_comfy_error_payload_validation_malformed_json():
    payload = server.comfy_error_payload("not a json")
    assert payload is None

def test_comfy_execution_error_payload_memory_exhausted():
    raw_error = {
        "exception_type": "cuda oom",
        "node_type": "KSampler",
        "exception_message": "out of memory"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_memory_exhausted"
    assert "記憶體不足" in payload["message"]

def test_comfy_execution_error_payload_memory_recovery_failed():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "Unknown",
        "exception_message": "memory_recovery_failed"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "memory_recovery_failed"
    assert "無法騰出 H3 所需記憶體" in payload["message"]

def test_comfy_execution_error_payload_timeout():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "Unknown",
        "exception_message": "timed out"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_timeout"
    assert "等待過久而停止" in payload["message"]

def test_comfy_execution_error_payload_input_missing():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "LoadImage",
        "exception_message": "no such file"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_input_missing"
    assert "找不到這次工作使用的輸入素材" in payload["message"]

def test_comfy_execution_error_payload_model_unavailable():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "CheckpointLoader",
        "exception_message": "model not found"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_model_unavailable"
    assert "這個工作流需要的模型目前不可用" in payload["message"]

def test_comfy_execution_error_payload_node_unavailable():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "Unknown",
        "exception_message": "class_type not found"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_node_unavailable"
    assert "這個工作流需要的節點目前不可用" in payload["message"]

def test_comfy_execution_error_payload_workflow_incompatible():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "VAEDecode",
        "exception_message": "shape mismatch"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_workflow_incompatible"
    assert "與目前模型或節點版本不相容" in payload["message"]

def test_comfy_execution_error_payload_general_execution_failed():
    raw_error = {
        "exception_type": "execution_error",
        "node_type": "Unknown",
        "exception_message": "Some other error"
    }
    payload = server.comfy_execution_error_payload(raw_error)
    assert payload["error_code"] == "generation_execution_failed"
    assert "生成已開始但沒有成功完成" in payload["message"]

def test_comfy_error_message():
    raw_error = json.dumps({
        "error": {
            "type": "prompt_outputs_failed_validation",
            "message": "Prompt outputs failed validation"
        }
    })
    msg = server.comfy_error_message(raw_error)
    assert "與目前節點版本不相容" in msg

    msg = server.comfy_error_message("not a json")
    assert "生成服務回傳無法辨識的錯誤" in msg
