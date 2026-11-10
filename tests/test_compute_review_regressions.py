"""Independent source-contract regressions; all status and media data synthetic."""
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_web import ComputeWeb


def local_web(*, local=None, history=None, direct=None):
    web = object.__new__(ComputeWeb)
    web.user = {'email': 'synthetic-owner@example.test', 'role': 'user'}
    task_id = 'synthetic-task'
    def request_json(path):
        if path.startswith('/history/'):
            return {task_id: history} if history else {}
        if path == '/queue':
            return {'queue_running': [], 'queue_pending': []}
        raise AssertionError(path)
    web.s = SimpleNamespace(
        user_can_access_job=lambda *args: True,
        local_job_snapshot=lambda: [dict(local, promptId=task_id)] if local else [],
        request_json=request_json,
        direct_sidecar_for_prompt=lambda task: ('/synthetic/sidecar', direct) if direct else None,
        direct_sidecar_owner_email=lambda *args: web.user['email'],
        direct_history_detail=lambda *args: direct,
        user_can_access_view_item=lambda *args: True,
    )
    return web, task_id


def completed_history():
    return {'status': {'completed': True, 'status_str': 'success'},
            'outputs': {'save': {'images': [{'filename': 'synthetic.png', 'subfolder': 'Synthetic', 'type': 'output'}]}}}


@pytest.mark.parametrize(('state', 'expected'), [('running', 'running'), ('error', 'failed'), ('cancelled', 'cancelled')])
def test_wrapper_lifecycle_wins_over_raw_comfy_completion(state, expected):
    # H3 and identity-photo wrappers validate/adopt after Comfy is completed.
    # A raw history response cannot publish or freeze a successful wrapper result.
    web, task_id = local_web(local={'state': state, 'jobKind': 'minimax_h3_web'}, history=completed_history())
    result = web.status_local(web.user['email'], task_id)
    assert result['state'] == expected
    assert not result.get('artifacts')


def test_local_result_link_carries_exact_prompt_ownership_hint():
    # /api/view needs promptId to prove membership from history when no sidecar
    # or background library association has been created yet.
    web, task_id = local_web(history=completed_history())
    result = web.status_local(web.user['email'], task_id)
    query = parse_qs(urlsplit(result['artifacts'][0]['url']).query)
    assert query.get('promptId') == [task_id]


def test_direct_local_completion_uses_existing_sidecar_output_contract():
    web, task_id = local_web(local={'state': 'completed'}, direct={
        'completed': True, 'status': 'completed',
        'outputs': [{'filename': 'synthetic.wav', 'subfolder': 'Synthetic', 'type': 'output'}]})
    result = web.status_local(web.user['email'], task_id)
    assert result['state'] == 'completed'
    assert result.get('artifacts') and result['artifacts'][0]['name'] == 'synthetic.wav'


@pytest.mark.parametrize('kind', ['minimax_h3_web', 'clipmode_character_photoshoot_web'])
def test_restarted_wrapper_cannot_publish_raw_comfy_output_without_verified_sidecar(kind):
    web, task_id = local_web(history=completed_history())
    web.s.load_job_index = lambda: {task_id: {'jobKind': kind}}
    result = web.status_local(web.user['email'], task_id)
    assert result['state'] == 'submission_uncertain' and not result.get('artifacts')


@pytest.mark.parametrize(('source', 'expected'), [
    ('pending', 'queued'), ('running', 'running'), ('cancel_requested', 'cancelling'),
    ('failed', 'failed'), ('cancelled', 'cancelled'), ('completed', 'collecting'),
])
def test_voice_orchestrator_uses_actual_run_contract_without_comfy(source, expected):
    web, _ = local_web()
    run_id = 'run_' + 'a' * 20
    web.s.request_json = lambda *args: pytest.fail('orchestrator status cannot depend on Comfy')
    web.s.castle_orchestrator = SimpleNamespace(get_run_api=lambda task: {
        'run': {'id': task, 'workflow_type': 'castle.voxcpm_tts', 'status': source}})
    result = web.status_local(web.user['email'], run_id)
    assert result == {'state': expected, 'downstream_id': run_id}


def test_voice_orchestrator_completion_returns_only_owned_sidecar_outputs():
    web, _ = local_web(direct={'completed': True, 'outputs': [
        {'filename': 'voice.wav', 'subfolder': 'Synthetic', 'type': 'output'}]})
    run_id = 'run_' + 'a' * 20
    web.s.castle_orchestrator = SimpleNamespace(get_run_api=lambda task: {
        'run': {'id': task, 'workflow_type': 'castle.voice_speak_guarded', 'status': 'completed'}})
    result = web.status_local(web.user['email'], run_id)
    assert result['state'] == 'completed' and result['artifacts'][0]['name'] == 'voice.wav'


def test_orchestrator_cancel_checks_owner_and_waits_for_acknowledgement():
    web, _ = local_web()
    run_id = 'run_' + 'a' * 20
    state, calls = ['running'], []
    def get_run(task):
        calls.append(('get', task))
        return {'run': {'id': task, 'workflow_type': 'castle.voxcpm_tts', 'status': state[0]}}
    def cancel(task):
        calls.append(('cancel', task))
        state[0] = 'cancel_requested'
        return {'changed': 1}
    web.s.castle_orchestrator = SimpleNamespace(get_run_api=get_run, cancel_run_api=cancel)
    result = web.cancel_local(web.user['email'], run_id)
    assert result == {'state': 'cancelling', 'downstream_id': run_id}
    assert [call[0] for call in calls] == ['get', 'cancel', 'get']
    web.s.user_can_access_job = lambda *args: False
    calls.clear()
    with pytest.raises(PermissionError):
        web.cancel_local('other-owner', run_id)
    assert not calls


def test_orchestrator_lookup_cannot_expand_to_other_workflows_or_replace_identity():
    web, _ = local_web()
    run_id = 'run_' + 'a' * 20
    for run in ({'id': run_id, 'workflow_type': 'unrelated', 'status': 'running'},
                {'id': 'run_' + 'b' * 20, 'workflow_type': 'castle.voxcpm_tts', 'status': 'running'}):
        web.s.castle_orchestrator = SimpleNamespace(get_run_api=lambda task: {'run': run},
            cancel_run_api=lambda *args: pytest.fail('invalid workflow must never be cancelled'))
        with pytest.raises(PermissionError):
            web.cancel_local(web.user['email'], run_id)


def test_actual_unsigned_voice_sidecar_uses_final_run_paths_and_exact_owner(tmp_path):
    web, _ = local_web()
    run_id = 'run_' + 'a' * 20
    output_root = tmp_path / 'output'
    output = output_root / 'Castle' / 'voice.wav'
    sidecar = Path(str(output) + '.castle.json')
    data = {'promptId': run_id, 'status': 'completed', 'outputs': [
        {'filename': 'voice.wav', 'subfolder': 'tmp', 'type': 'output'}]}
    web.s.COMFY_OUTPUT = output_root
    web.s.direct_sidecar_for_prompt = lambda task: (sidecar, data)
    web.s.direct_sidecar_owner_email = lambda *args: ''
    web.s.direct_history_detail = lambda *args: {**data, 'completed': True}
    web.s.user_can_access_job = lambda user, task, metadata=None, index=None: metadata is None
    web.s.user_can_access_view_item = lambda user, filename, subfolder, *args: subfolder == 'Castle'
    run = {'id': run_id, 'workflow_type': 'castle.voxcpm_tts', 'status': 'completed',
           'input': {'output_path': str(output), 'sidecar_path': str(sidecar)}}
    web.s.castle_orchestrator = SimpleNamespace(get_run_api=lambda task: {'run': run})
    result = web.status_local(web.user['email'], run_id)
    assert result['state'] == 'completed'
    assert 'subfolder=Castle' in result['artifacts'][0]['url'] and 'tmp' not in result['artifacts'][0]['url']
    data['provenance'] = {'signature': 'invalid-synthetic-signature'}
    with pytest.raises(PermissionError):
        web.status_local(web.user['email'], run_id)
    del data['provenance']
    run['input']['output_path'] = str(output_root / 'different.wav')
    with pytest.raises(PermissionError):
        web.status_local(web.user['email'], run_id)
