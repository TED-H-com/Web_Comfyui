"""Owner-scoped facade over r26 ProviderConsole and the write-only vault references.

No environment/config fallback, arbitrary path argument or cross-user account IDs.
"""
from pathlib import Path
from .contracts import fail

def reference_for(console, owner, lane):
    if owner!=console.owner or lane not in {'autoip','runninghub','comfy_cloud'}:
        fail('credential_owner','只能使用自己的平台連線')
    from provider_fabric.config import SecretReference
    with console.prefs.db() as db:data=console._raw(lane,db)
    if data.get('actor_id')!=console.actor.user_id or not data.get('credential_present'):
        fail('credential_required','請先儲存自己的平台金鑰')
    if lane=='autoip':
        reference=console.runtime.store.credential_reference_for_actor(console.actor.user_id,data['account_id'])
    else:
        if not data.get('credential_ref') or not data.get('account_id'):
            fail('credential_migration_required','請先完成擁有者金鑰遷移')
        reference=SecretReference.from_mapping(data['credential_ref'])
    path=Path(reference.locator)
    expected=console.runtime.config.credential_root/lane/data['account_id']
    if path.parent!=expected or path.suffix!='.secret':
        fail('credential_owner','金鑰不屬於此帳號的保管庫')
    return reference
