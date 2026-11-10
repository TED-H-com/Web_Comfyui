"""Source-backed coverage inventory. This is not a readiness or price quote."""
from compute_adapters import SUPPORTED_FEATURES
from compute_autoip import TEXT_FEATURES
from compute_autoip_media import _OPERATIONS


def capabilities(features, hosted_config, templates):
    configured = {item['provider']: item for item in hosted_config.get('providers', [])}
    catalog = {item['feature_id'] for item in templates.get('templates', [])}
    unsupported = {item['feature_id']: item['reason'] for item in templates.get('unsupported', [])}
    result = []
    for feature in features:
        feature_id = feature['id']
        executable = bool(feature.get('builder')) and feature.get('apiEnabled') is not False
        providers = {}
        for provider in ('local', 'colab', 'autoip', 'comfy_cloud', 'runninghub'):
            state, reason = 'unsupported', '尚未提供相容的執行契約'
            extra = {}
            if not executable:
                reason = '此功能尚未開放生成'
            elif provider == 'local':
                state, reason = 'implemented', '已接上既有執行流程；模型與節點會在送出前檢查'
            elif provider == 'colab' and feature_id in SUPPORTED_FEATURES:
                state, reason = 'implemented', '已提供 PRISM / P-H3 轉接；仍需可用帳號與相容參數'
            elif provider == 'autoip' and feature_id in set(TEXT_FEATURES) | set(_OPERATIONS):
                state, reason = 'implemented', '已提供服務轉接；需帳號、報價與相容參數，角色綁定另行檢查'
            elif provider in ('comfy_cloud', 'runninghub'):
                config = configured.get(provider, {})
                recipe = next((item for item in config.get('recipes', []) if item['feature_id'] == feature_id), None)
                if recipe:
                    extra = {'recipe_id': recipe['recipe_id'], 'verified': recipe.get('verified') is True}
                    if config.get('enabled') and config.get('credential_configured'):
                        state = 'configured'
                        reason = '已設定；每次仍會檢查輸入相容性' if extra['verified'] else '已設定；尚需手動生成並驗證成果'
                    else:
                        state, reason = 'unconfigured', '已有工作流配方，尚需啟用平台並設定金鑰'
                elif feature_id in catalog:
                    state, reason = 'unconfigured', '已有內建配方可選，尚需設定金鑰與遠端工作流'
                elif feature_id in unsupported:
                    state, reason = 'unsupported', unsupported[feature_id]
                else:
                    state, reason = 'unconfigured', '通用介面可匯入配方，但此功能尚無內建或已設定的遠端配方'
            providers[provider] = {'status': state, 'reason': reason, **extra}
        result.append({'id': feature_id, 'title': feature.get('title', feature_id), 'providers': providers})
    return {'features': result, 'readiness_checked': False}
