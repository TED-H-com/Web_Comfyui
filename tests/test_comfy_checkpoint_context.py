"""Local checkpoint context and real admission-boundary regressions."""
import ast
import json
from pathlib import Path
from types import SimpleNamespace
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'webui'))
from comfy_checkpoint_context import (context_path, read_checkpoint_context,
    write_checkpoint_context, prompt_admission)
from scripts.checkpoint_comfy_history import checkpoint
from scripts import comfy_lifecycle as lifecycle
from compute_routing import ComputeRejected


@pytest.fixture
def context(tmp_path, monkeypatch):
    monkeypatch.setenv('CASTLE_COMFY_ADMISSION_LOCK', str(tmp_path / 'admission.lock'))
    monkeypatch.setenv('SYNTHETIC_PROVIDER_SECRET', 'MUST_NOT_PERSIST')
    namespace = {'__file__': str(tmp_path / 'app/webui/server.py'),
        'DEFAULT_COMFY': 'http://127.0.0.1:8188', 'COMFY_OUTPUT': tmp_path / 'output',
        'CHARACTER_LIBRARY': tmp_path / 'characters', 'PROVENANCE_KEY_PATH': tmp_path / 'provenance.key',
        'USERS_PATH': tmp_path / 'users.json'}
    value = write_checkpoint_context(namespace, tmp_path / 'configured-state/colab')
    return value


def test_configuration_is_private_exact_and_contains_no_ambient_secret(context):
    assert read_checkpoint_context() == context
    assert context_path().stat().st_mode & 0o777 == 0o600
    assert 'MUST_NOT_PERSIST' not in context_path().read_text()
    assert context['receipt_root'].endswith('configured-state/compute-local-results')


@pytest.mark.parametrize('unsafe', ['world-readable', 'symlink', 'unknown-field'])
def test_unsafe_context_is_not_loaded(context, unsafe):
    path = context_path()
    if unsafe == 'world-readable': path.chmod(0o644)
    elif unsafe == 'symlink':
        target = path.with_suffix('.other');path.rename(target);path.symlink_to(target)
    else:
        context['env']['UNTRUSTED_SECRET'] = 'bad'
        path.write_text(json.dumps(context))
    with pytest.raises((ValueError, OSError)):
        read_checkpoint_context()


def test_cli_rejects_wrong_runtime_before_loading_or_network(context):
    with pytest.raises(ValueError):
        checkpoint(context, 'http://127.0.0.1:8999', server_loader=lambda: pytest.fail('wrong runtime loaded'))


def test_cli_uses_explicit_receipt_root_and_suppresses_helper_output(context, capsys):
    history = {'synthetic-task': {'status': {'completed': True}}}
    def persist(server, actual, *, receipt_root):
        assert actual is history
        assert receipt_root == Path(context['receipt_root'])
        print('PRIVATE_PROMPT_SENTINEL')
        return {'persisted': 1, 'skipped': 0}
    server = SimpleNamespace(DEFAULT_COMFY=context['env']['COMFYUI_URL'], request_json=lambda path: history)
    assert checkpoint(context, server.DEFAULT_COMFY, server_loader=lambda: server, checkpoint_fn=persist) == {
        'ok': True, 'persisted': 1, 'skipped': 0}
    assert not capsys.readouterr().out


def _admission_blocks():
    tree = ast.parse((ROOT / 'webui/server.py').read_text())
    function = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == '_generate_result')
    blocks = [n for n in ast.walk(function) if isinstance(n, ast.With)
              and any(isinstance(x.context_expr, ast.Call) and isinstance(x.context_expr.func, ast.Name)
                      and x.context_expr.func.id == 'prompt_admission' for x in n.items)]
    assert len(blocks) == 3
    return blocks


@pytest.mark.parametrize('index', range(3))
def test_actual_web_post_and_attribution_share_one_gate(context, index):
    calls = []
    def require_gate():
        with pytest.raises(lifecycle.AdmissionBusy):
            with lifecycle.recycle_lock(timeout_seconds=0):
                pytest.fail('recycle raced a web admission')
    def submit(*args):
        require_gate(); calls.append('post');return {'prompt_id': 'synthetic-task'}
    def attribute(*args, **kwargs):
        require_gate(); calls.append('attribution')
    handler = SimpleNamespace(_compute_dispatch_started=False)
    namespace = {'prompt_admission': prompt_admission, 'self': handler, 'request_json': submit,
        'record_job_attribution': attribute, 'payload': {}, 'submitted_by': {}, 'character_id': '',
        'feature_id': 'text2img', 'action_id': '', 'variant_id': '', 'character_context': None}
    code = compile(ast.fix_missing_locations(ast.Module(body=[_admission_blocks()[index]], type_ignores=[])), 'actual-web-admission', 'exec')
    with lifecycle.recycle_lock(timeout_seconds=0):
        with pytest.raises(ComputeRejected) as error:exec(code, namespace)
    assert error.value.code == 'capacity_exhausted' and calls == []
    assert not handler._compute_dispatch_started
    exec(code, namespace)
    assert calls == ['post', 'attribution'] and handler._compute_dispatch_started


def test_context_from_previous_process_generation_is_rejected(context):
    context['web_start'] = '0'
    context_path().write_text(json.dumps(context))
    with pytest.raises(ValueError):read_checkpoint_context()
