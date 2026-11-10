import concurrent.futures
import json
from pathlib import Path
import sqlite3
import sys
import threading
import uuid

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from compute_routing import (
    ComputeAdapter, ComputeRejected, ComputeRouter, ComputeRoutingError,
    ComputeValidationError,
)


def adapter(calls, *, lane='local', eligible=True, queue=0, cost=None, slots=None, submit=None, status=None, cancel=None):
    def inspect(owner, request, limits):
        return {'eligible': eligible, 'reasons': [] if eligible else ['unsupported_operation'],
                'queue_depth': queue, 'available_slots': slots, 'estimated_cost': cost,
                'currency': {'local': 'local', 'colab': 'compute_units', 'autoip': 'credits'}[lane]}
    def send(owner, request_id, request, limits):
        calls.append((lane, owner, request_id, request, limits))
        if submit:
            return submit(owner, request_id, request, limits)
        return {'state': 'queued', 'downstream_id': request_id, 'status_url': '/api/test/status'}
    return ComputeAdapter(inspect, send, status, cancel)


def new_id():
    return str(uuid.uuid4())


def test_manual_lane_and_automatic_capacity_comparison(tmp_path):
    calls = []
    router = ComputeRouter(tmp_path / 'routing.db', {
        'local': adapter(calls, queue=4),
        'colab': adapter(calls, lane='colab', cost=20),
        'autoip': adapter(calls, lane='autoip', cost=2),
    })
    planned = router.preview('owner', {'featureId': 'text2img'})
    # 2 AutoIP credits and 20 Colab units are incomparable; queue ties use the
    # declared stable lane priority, not the smallest-looking price number.
    assert planned['selected_lane'] == 'colab' and calls == []
    assert router.preview('owner', {}, 'local')['selected_lane'] == 'local'
    result = router.submit('owner', new_id(), {}, 'autoip', {'max_credits': 2})
    assert result['lane'] == 'autoip' and result['state'] == 'queued'
    assert len(calls) == 1


@pytest.mark.parametrize('lane,currency,key', [('comfy_cloud', 'comfy_credits', 'max_comfy_credits'),
                                             ('runninghub', 'runninghub_coins', 'max_runninghub_coins')])
def test_hosted_lanes_keep_currency_and_manual_verification_separate(tmp_path, lane, currency, key):
    calls = []
    qualified = [False]
    def inspect(*args):
        return {'eligible': True, 'auto_eligible': qualified[0], 'queue_depth': 0,
                'estimated_cost': 3.5, 'currency': currency, 'budget_basis': 'configured_estimate'}
    def submit(owner, rid, body, limits):
        calls.append(rid)
        return {'state': 'queued', 'downstream_id': rid}
    router = ComputeRouter(tmp_path / 'routing.db', {lane: ComputeAdapter(inspect, submit)})
    preview = router.preview('owner', {}, limits={key: 4})
    row = next(row for row in preview['candidates'] if row['lane'] == lane)
    assert row['eligible'] and not row['auto_eligible'] and row['currency'] == currency
    assert row['budget_basis'] == 'configured_estimate' and preview['selected_lane'] is None
    assert router.preview('owner', {}, lane, {key: 3})['selected_lane'] is None
    assert router.submit('owner', new_id(), {}, lane, {key: 4})['lane'] == lane
    assert len(calls) == 1
    qualified[0] = True
    assert router.preview('owner', {}, 'auto', {key: 4})['selected_lane'] == lane


def test_unknown_feature_inspection_rejects_instead_of_claiming_universal_support(tmp_path):
    calls = []
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, eligible=False)})
    result = router.preview('owner', {'featureId': 'unimplemented'})
    assert result['selected_lane'] is None
    assert result['candidates'][0]['reasons'] == ['unsupported_operation']
    with pytest.raises(ComputeRejected, match='沒有符合'):
        router.submit('owner', new_id(), {'featureId': 'unimplemented'})
    assert calls == []


@pytest.mark.parametrize('lane,limits,cost,expected', [
    ('autoip', {'max_credits': 9}, 10, 'budget_exceeded'),
    ('colab', {'max_units': 9}, 10, 'budget_exceeded'),
    ('autoip', {'max_credits': 9}, None, 'quote_unavailable'),
])
def test_budget_checked_in_own_provider_currency(tmp_path, lane, limits, cost, expected):
    router = ComputeRouter(tmp_path / 'routing.db', {lane: adapter([], lane=lane, cost=cost)})
    result = router.preview('owner', {}, lane, limits)
    row = next(row for row in result['candidates'] if row['lane'] == lane)
    assert expected in row['reasons'] and result['selected_lane'] is None


def test_inspection_errors_fail_closed_without_exposing_provider_details(tmp_path):
    def inspect(*args):
        raise ValueError('private provider URL or credential')
    router = ComputeRouter(tmp_path / 'routing.db', {'autoip': ComputeAdapter(inspect, lambda *args: pytest.fail())})
    result = router.preview('owner', {})
    assert 'private' not in json.dumps(result)
    assert result['candidates'][2]['reasons'] == ['inspection_unavailable']


def test_exact_retry_is_durable_and_conflicting_request_rejected(tmp_path):
    calls, request_id = [], new_id()
    adapters = {'local': adapter(calls), 'colab': adapter(calls, lane='colab')}
    router = ComputeRouter(tmp_path / 'routing.db', adapters)
    first = router.submit('owner', request_id, {'featureId': 'text2img', 'settings': {'seed': 7}})
    restarted = ComputeRouter(tmp_path / 'routing.db', adapters)
    assert restarted.submit('owner', request_id, {'settings': {'seed': 7}, 'featureId': 'text2img'}) == first
    for request, placement in [({'featureId': 'changed'}, 'auto'), ({'featureId': 'text2img', 'settings': {'seed': 7}}, 'colab')]:
        with pytest.raises(ComputeRoutingError) as error:
            restarted.submit('owner', request_id, request, placement)
        assert error.value.code == 'request_conflict' and error.value.status == 409
    assert len(calls) == 1
    assert 'settings' not in first and 'owner' not in first


def test_owner_scope_and_no_cross_owner_status_or_cancel(tmp_path):
    calls, request_id = [], new_id()
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls)})
    router.submit('first', request_id, {})
    for operation in [router.status, router.cancel]:
        with pytest.raises(ComputeRoutingError) as error:
            operation('second', request_id)
        assert error.value.status == 404
    router.submit('second', request_id, {})
    assert len(calls) == 2


def test_simultaneous_duplicate_only_dispatches_once(tmp_path):
    entered, release = threading.Event(), threading.Event()
    calls, request_id = [], new_id()
    def send(*args):
        entered.set()
        assert release.wait(3)
        return {'state': 'queued', 'downstream_id': 'original'}
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, submit=send)})
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(router.submit, 'owner', request_id, {})
        assert entered.wait(3)
        duplicate = router.submit('owner', request_id, {})
        assert duplicate['state'] == 'submission_uncertain'
        release.set()
        assert first.result(timeout=3)['downstream_id'] == 'original'
    assert len(calls) == 1


def test_uncertain_submission_stays_on_original_lane_after_restart(tmp_path):
    calls, request_id = [], new_id()
    def fail(*args):
        raise TimeoutError('private endpoint timeout detail')
    adapters = {'local': adapter(calls, submit=fail), 'autoip': adapter(calls, lane='autoip')}
    router = ComputeRouter(tmp_path / 'routing.db', adapters)
    result = router.submit('owner', request_id, {})
    assert result['state'] == 'submission_uncertain' and result['lane'] == 'local'
    assert 'private' not in json.dumps(result)
    restarted = ComputeRouter(tmp_path / 'routing.db', adapters)
    assert restarted.submit('owner', request_id, {}) == result
    assert restarted.status('owner', request_id, refresh=True) == result
    assert len(calls) == 1


def test_process_death_after_dispatch_claim_is_never_retried(tmp_path):
    calls, request_id = [], new_id()
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls)})
    router.submit('owner', request_id, {})
    with sqlite3.connect(router.path) as db:
        db.execute("UPDATE compute_requests SET state='dispatching',receipt_json='{}'")
    result = router.submit('owner', request_id, {})
    assert result['state'] == 'submission_uncertain' and len(calls) == 1


def test_definitive_validation_failure_is_not_ambiguous_or_fallback(tmp_path):
    calls = []
    def reject(*args):
        raise ComputeValidationError('請選擇支援的尺寸', code='unsupported_size')
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, submit=reject), 'autoip': adapter(calls, lane='autoip')})
    result = router.submit('owner', new_id(), {})
    assert result['state'] == 'rejected' and result['error']['code'] == 'unsupported_size'
    assert len(calls) == 1 and calls[0][0] == 'local'


def test_concurrency_limit_released_only_by_verified_terminal_result(tmp_path):
    calls = []
    status = lambda owner, task: {'state': 'completed', 'downstream_id': task, 'result_url': '/api/results/original'}
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, status=status)})
    first = router.submit('owner', new_id(), {}, limits={'max_concurrent': 1})
    with pytest.raises(ComputeRejected):
        router.submit('owner', new_id(), {}, limits={'max_concurrent': 1})
    assert router.status('owner', first['request_id'], refresh=True)['state'] == 'completed'
    router.submit('owner', new_id(), {}, limits={'max_concurrent': 1})
    assert len(calls) == 2


def test_status_cannot_replace_downstream_identity_or_leak_exception(tmp_path):
    calls = []
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, status=lambda *args: {'state': 'completed', 'downstream_id': 'different'})})
    result = router.submit('owner', new_id(), {})
    refreshed = router.status('owner', result['request_id'], refresh=True)
    assert refreshed['state'] == 'queued' and refreshed['downstream_id'] == result['downstream_id']


def test_cancel_delegates_exact_owner_task_and_never_resubmits(tmp_path):
    calls, cancelled = [], []
    def cancel(owner, task):
        cancelled.append((owner, task))
        return {'state': 'cancelled', 'downstream_id': task}
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, cancel=cancel)})
    result = router.submit('owner', new_id(), {})
    assert router.cancel('owner', result['request_id'])['state'] == 'cancelled'
    router.cancel('owner', result['request_id'])
    assert len(calls) == len(cancelled) == 1


def test_untrusted_receipt_is_uncertain_and_never_exposes_remote_url(tmp_path):
    calls = []
    def send(*args):
        return {'state': 'queued', 'downstream_id': 'task', 'status_url': 'https://private.invalid', 'raw_provider_payload': 'private'}
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls, submit=send)})
    result = router.submit('owner', new_id(), {})
    assert result['state'] == 'submission_uncertain' and 'private' not in json.dumps(result)


@pytest.mark.parametrize('limits', [{'max_units': float('nan')}, {'max_credits': -1}, {'max_minutes': True}, {'max_concurrent': 0}, {'other': 2}])
def test_invalid_limits_rejected_before_inspection(tmp_path, limits):
    router = ComputeRouter(tmp_path / 'routing.db', {'local': ComputeAdapter(lambda *a: pytest.fail(), lambda *a: pytest.fail())})
    with pytest.raises(ComputeRoutingError):
        router.preview('owner', {}, limits=limits)


def test_colab_account_and_gpu_are_validated_and_immutably_bound(tmp_path):
    calls, request_id = [], new_id()
    router = ComputeRouter(tmp_path / 'routing.db', {'colab': adapter(calls, lane='colab')})
    limits = {'account_id': new_id(), 'gpu': 'A100'}
    router.submit('owner', request_id, {}, 'colab', limits)
    assert calls[0][4] == limits
    with pytest.raises(ComputeRoutingError) as error:
        router.submit('owner', request_id, {}, 'colab', {**limits, 'gpu': 'H100'})
    assert error.value.code == 'request_conflict'
    for invalid in ({'account_id': 'path'}, {'gpu': 'unknown'}):
        with pytest.raises(ComputeRoutingError):
            router.preview('owner', {}, limits=invalid)


def test_result_artifacts_preserve_owner_scoped_urls_and_recipe_metadata(tmp_path):
    def send(*args):
        return {'state': 'completed', 'downstream_id': 'done', 'recipe_label': 'Colab H3',
                'artifacts': [{'name': 'output.png', 'url': '/api/colab/jobs/one/files/output.png',
                               'size': 100, 'private_path': 'never-publish'}]}
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter([], submit=send)})
    result = router.submit('owner', new_id(), {})
    assert result['recipe_label'] == 'Colab H3'
    assert result['artifacts'][0]['name'] == 'output.png'
    assert 'never-publish' not in json.dumps(result)


def test_list_is_owner_scoped_and_excludes_request_payload(tmp_path):
    calls = []
    router = ComputeRouter(tmp_path / 'routing.db', {'local': adapter(calls)})
    router.submit('first', new_id(), {'settings': {'prompt': 'private text'}})
    router.submit('second', new_id(), {})
    rows = router.list_jobs('first')
    assert len(rows) == 1 and 'private text' not in json.dumps(rows)
    assert 'request_json' not in rows[0]


def test_history_cursor_orders_timestamp_ties_without_duplicates_or_newer_inserts(tmp_path):
    now = [1000.0]
    lane = adapter([], submit=lambda owner, rid, request, limits: {'state': 'completed', 'downstream_id': rid})
    router = ComputeRouter(tmp_path / 'history.db', {'local': lane}, clock=lambda: now[0])
    ids = [str(uuid.UUID(int=index)) for index in range(1, 124)]
    for rid in ids[::2] + ids[1::2]:
        router.submit('owner', rid, {})
    page = router.list_jobs('owner', limit=50)
    seen = [row['request_id'] for row in page]
    assert seen == sorted(ids, reverse=True)[:50]
    now[0] = 2000.0
    newer = str(uuid.UUID(int=1000))
    router.submit('owner', newer, {})
    while page:
        page = router.list_jobs('owner', limit=50, before=page[-1]['request_id'])
        seen.extend(row['request_id'] for row in page)
    assert seen == sorted(ids, reverse=True)
    assert len(set(seen)) == 123 and newer not in seen
    assert router.list_jobs('owner', limit=1)[0]['request_id'] == newer


def test_history_cursor_is_canonical_and_owned(tmp_path):
    router = ComputeRouter(tmp_path / 'history.db', {'local': adapter([])})
    foreign = new_id()
    router.submit('other-owner', foreign, {})
    for value in (foreign, new_id()):
        with pytest.raises(ComputeRoutingError) as error:
            router.list_jobs('owner', before=value)
        assert error.value.code == 'not_found' and error.value.status == 404
    with pytest.raises(ComputeRoutingError) as error:
        router.list_jobs('owner', before='not-a-uuid')
    assert error.value.status == 400


def test_recovery_looks_up_original_request_without_submitting_again(tmp_path):
    calls, lookups, request_id = [], [], new_id()
    def fail(*args):
        raise TimeoutError()
    def recover(owner, original_id):
        lookups.append((owner, original_id))
        return {'state': 'queued', 'downstream_id': original_id}
    base = adapter(calls, submit=fail)
    lane = ComputeAdapter(base.inspect, base.submit, recover=recover)
    router = ComputeRouter(tmp_path / 'routing.db', {'local': lane})
    assert router.submit('owner', request_id, {})['state'] == 'submission_uncertain'
    recovered = router.status('owner', request_id, refresh=True)
    assert recovered['downstream_id'] == request_id and 'error' not in recovered
    assert len(calls) == 1 and lookups == [('owner', request_id)]


def test_recipe_warnings_are_visible_without_turning_compatibility_into_failure(tmp_path):
    lane = ComputeAdapter(lambda *args: {'eligible': True, 'warnings': ['改用平台固定配方']}, lambda *args: None)
    router = ComputeRouter(tmp_path / 'routing.db', {'local': lane})
    result = router.preview('owner', {})
    assert result['selected_lane'] == 'local'
    assert result['candidates'][0]['warnings'] == ['改用平台固定配方']
