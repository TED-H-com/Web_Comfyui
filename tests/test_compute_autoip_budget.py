from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'webui'))
from provider_fabric.service import ActorContext, ProviderFabricService, ProviderServiceError


def service_fixture(monkeypatch, credits):
    """Drive actual submission control flow with an inert signed-plan boundary."""
    service = object.__new__(ProviderFabricService)
    actor = ActorContext('owner', 'operator')
    confirms = []
    monkeypatch.setattr(service, '_require_actor', lambda value: value)
    monkeypatch.setattr(service, '_generation_plan_nonce', lambda owner, request: 'a' * 32)
    monkeypatch.setattr(service, '_find_generation_replay', lambda *args: None)
    def plan(actor, request, **kwargs):
        return {'plan_id': 'plan:' + 'a' * 32, 'plan_token': 'inert-test-plan',
                'quote': {'credits': credits}, 'decision': {'selected_provider_id': 'autoip'}}
    monkeypatch.setattr(service, '_plan_intent', plan)
    def confirm(actor, body):
        confirms.append(body)
        return {'state': 'created'}
    monkeypatch.setattr(service, 'confirm_intent', confirm)
    monkeypatch.setattr(service, '_generation_result', lambda request_id, confirmation, decision, quote: confirmation)
    return service, actor, confirms


def test_explicit_autoip_cap_rejects_current_quote_before_any_confirmation_or_outbox(monkeypatch):
    service, actor, confirms = service_fixture(monkeypatch, 31)
    with pytest.raises(ProviderServiceError) as error:
        service.submit_generation(actor, {'schema_version': 1, 'request_id': 'generation:one', 'plan': {'max_credits': 30}})
    assert error.value.code == 'spend_limit_exceeded'
    assert confirms == []  # confirm_intent is the sole durable outbox writer.


@pytest.mark.parametrize('maximum', [31, 32, None])
def test_exact_boundary_and_unlimited_generation_keep_same_quote(monkeypatch, maximum):
    service, actor, confirms = service_fixture(monkeypatch, 31)
    assert service.submit_generation(actor, {'schema_version': 1, 'request_id': 'generation:one', 'plan': {'max_credits': maximum}})['state'] == 'created'
    assert len(confirms) == 1 and confirms[0]['accepted_credits'] == 31
    assert confirms[0]['plan_token'] == 'inert-test-plan'


def test_recovery_reads_owned_binding_without_quote_or_submission(monkeypatch):
    service, actor, confirms = service_fixture(monkeypatch, 31)
    request_id = 'generation:one'
    plan_id = 'plan:' + 'a' * 32
    intent_id = service._generation_storage_ids(plan_id)['intent_id']
    monkeypatch.setattr(service, '_plan_intent', lambda *args, **kwargs: pytest.fail('recovery must not quote'))
    monkeypatch.setattr(service, 'confirm_intent', lambda *args, **kwargs: pytest.fail('recovery must not confirm'))
    monkeypatch.setattr(service, 'get_intent_status', lambda who, iid: {'intent_id': iid, 'state': 'queued'})
    service._store = SimpleNamespace(get_intent=lambda iid: {'payload': {
        'owner_user_id': 'owner', 'plan_id': plan_id, 'generation_request': {'request_id': request_id},
        'creative_instruction': 'must-not-return',
    }})
    result = service.recover_generation_request(actor, request_id)
    assert result == {'intent_id': intent_id, 'state': 'queued', 'request_id': request_id}
    assert confirms == []


def test_recovery_absent_or_other_owner_remains_unknown(monkeypatch):
    service, actor, _ = service_fixture(monkeypatch, 31)
    def missing(*args):
        raise ProviderServiceError('intent_not_found', 'not found', status=404)
    monkeypatch.setattr(service, 'get_intent_status', missing)
    service._store = SimpleNamespace(get_intent=lambda iid: pytest.fail('must authorize before payload read'))
    assert service.recover_generation_request(actor, 'generation:one') is None


def test_recovery_rejects_mismatched_stored_owner_even_with_inconsistent_status(monkeypatch):
    service, actor, _ = service_fixture(monkeypatch, 31)
    monkeypatch.setattr(service, 'get_intent_status', lambda *args: {'state': 'queued'})
    service._store = SimpleNamespace(get_intent=lambda iid: {'payload': {
        'owner_user_id': 'other', 'plan_id': 'plan:' + 'a' * 32,
        'generation_request': {'request_id': 'generation:one'},
    }})
    with pytest.raises(ProviderServiceError) as error:
        service.recover_generation_request(actor, 'generation:one')
    assert error.value.code == 'generation_recovery_unavailable'
