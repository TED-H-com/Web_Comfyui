"""Authenticated WebUI adapters over the existing durable execution lanes."""
from pathlib import Path
from urllib.parse import urlencode, quote

from compute_routing import ComputeAdapter, ComputeRouter, ComputeRejected, ComputeRoutingError
import compute_adapters as colab_adapter


class ComputeWeb:
    def __init__(self, handler, server):
        self.h, self.s = handler, server
        self.user = handler.current_user() or {}
        self.owner = str(self.user.get('email') or '')
        if not self.owner:
            raise PermissionError('請先登入')
        self.colab = server.get_colab_service()
        from compute_preferences import ComputePreferences
        self.preferences = ComputePreferences(self.colab.root.parent / 'compute-preferences.sqlite3')
        from hosted_compute import HostedComputeService
        self.hosted = (server.get_hosted_compute_service() if hasattr(server, 'get_hosted_compute_service')
                       else HostedComputeService(self.colab.root.parent / 'hosted-compute'))
        self.router = ComputeRouter(self.colab.root.parent / 'compute-routing.sqlite3', {
            'local': ComputeAdapter(self.inspect_local, self.submit_local, self.status_local, self.cancel_local),
            'colab': ComputeAdapter(self.inspect_colab, self.submit_colab, self.status_colab, self.cancel_colab,
                                    recover=self.recover_colab),
            'autoip': ComputeAdapter(self.inspect_autoip, self.submit_autoip, self.status_autoip, recover=self.recover_autoip),
            **{provider: ComputeAdapter(
                lambda owner, request, limits, p=provider: self.inspect_hosted(p, owner, request, limits),
                lambda owner, rid, request, limits, p=provider: self._run(request, p, rid, limits),
                lambda owner, rid, p=provider: self.hosted.status(owner, p, rid),
                lambda owner, rid, p=provider: self.hosted.cancel(owner, p, rid),
                recover=lambda owner, rid, p=provider: self.hosted.recover(owner, p, rid),
            ) for provider in ('comfy_cloud', 'runninghub')},
        })

    def inspect_hosted(self, provider, owner, request, limits):
        self._feature(request)
        try:
            plan = self.s.plan_colab_generation_request(request, self.user,
                self.h.is_orchestrator_service_authenticated(),
                provider_actor=getattr(self.h, '_provider_runtime_actor', None), provider=provider)
            effective = {'featureId': request['featureId'], 'settings': plan['settings'],
                         'files': {slot: {} for slot in plan['file_slots']}}
            return self.hosted.inspect(owner, provider, effective, limits)
        except (ValueError, PermissionError, KeyError):
            return {'eligible': False, 'auto_eligible': False, 'reasons': ['input_validation_failed'],
                    'currency': 'comfy_credits' if provider == 'comfy_cloud' else 'runninghub_coins'}

    def _feature(self, request):
        feature = self.s.FEATURE_BY_ID.get(request.get('featureId'))
        if not feature or feature.get('apiEnabled') is False or not feature.get('builder'):
            raise ComputeRejected('這項生產工具尚未開放', code='feature_unavailable')
        if feature.get('operatorOnly') and not self.s.is_operator_user(self.user):
            raise ComputeRejected('這項功能需要操作員權限', code='operator_required', status=403)
        return feature

    def inspect_local(self, owner, request, limits):
        feature = self._feature(request)
        status = next((x for x in self.s.feature_status() if x['id'] == feature['id']), {})
        queue = self.s.request_json('/queue')
        active = {str(item[1]): True for key in ('queue_running', 'queue_pending')
                  for item in queue.get(key) or [] if isinstance(item, (list, tuple)) and len(item) > 1}
        for job in getattr(self.s, 'local_job_snapshot', lambda: [])():
            if job.get('promptId'):
                active[job['promptId']] = job.get('state') in {'pending', 'running', 'cancel_requested'}
        for job in getattr(self.s, 'orchestrator_job_snapshot', lambda: [])():
            if job.get('promptId') and job.get('lane') == 'gpu':
                active[job['promptId']] = job.get('state') in {'pending', 'running', 'cancel_requested'}
        count = sum(active.values())
        return {'eligible': bool(status.get('ready')), 'reasons': [] if status.get('ready') else ['local_not_ready'],
                'queue_depth': count, 'estimated_cost': 0, 'currency': 'local'}

    def _colab_plan(self, request):
        plan = self.s.plan_colab_generation_request(
            request, self.user, self.h.is_orchestrator_service_authenticated(),
            provider_actor=getattr(self.h, "_provider_runtime_actor", None))
        settings, warnings = colab_adapter.normalise_intent(plan['feature_id'], plan['settings'])
        compatibility = colab_adapter.colab_compatibility(plan['feature_id'], settings, plan['file_slots'])
        return compatibility, warnings

    def _colab_accounts(self, limits):
        snapshot = self.colab.snapshot(self.owner)
        busy = {job['account_id'] for job in snapshot['jobs'] if job['phase'] not in {'completed', 'failed', 'cancelled'}}
        accounts = [account for account in snapshot['accounts']
                    if account.get('authenticated') and account.get('id') not in busy
                    and float(account.get('balance_compute_units') or 0) >= limits.get('max_units', 20)
                    and (not limits.get('account_id') or account['id'] == limits['account_id'])]
        return snapshot, sorted(accounts, key=lambda a: (-float(a.get('balance_compute_units') or 0), a['id']))

    def inspect_colab(self, owner, request, limits):
        self._feature(request)
        reasons, warnings = [], []
        try:
            compatibility, warnings = self._colab_plan(request)
            if not compatibility['supported']:
                reasons.append(compatibility['code'])
        except colab_adapter.AdapterError as error:
            reasons.append(error.code)
        except (ValueError, PermissionError, KeyError):
            reasons.append('colab_input_invalid' if request.get('featureId') in colab_adapter.SUPPORTED_FEATURES else 'unsupported_feature')
        snapshot, accounts = self._colab_accounts(limits)
        if not snapshot['installed']: reasons.append('colab_not_installed')
        if not accounts: reasons.append('colab_account_unavailable')
        return {'eligible': not reasons, 'reasons': reasons, 'available_slots': len(accounts),
                'queue_depth': 0, 'estimated_cost': limits.get('max_units', 20), 'currency': 'compute_units', 'warnings': warnings}

    def _run(self, request, lane, request_id, limits):
        self.h._compute_dispatch_started = False
        try:
            return self.h._generate_result(request, compute_lane=lane, request_id=request_id, compute_limits=limits)
        except (ValueError, PermissionError) as error:
            if self.h._compute_dispatch_started:
                raise
            # Validation messages here are existing authored application messages.
            message = str(error) if isinstance(error, colab_adapter.AdapterError) else '設定或素材未通過檢查，沒有提交生成。'
            raise ComputeRejected(message, code=getattr(error, 'code', 'input_validation_failed')) from None

    def submit_local(self, owner, request_id, request, limits):
        result = self._run(request, 'local', request_id, limits)
        task_id = result.get('prompt_id') or result.get('promptId') or result.get('run_id')
        return {'state': 'queued', 'downstream_id': task_id, 'recipe_id': 'local:' + request['featureId'],
                'recipe_label': '本機既有工作流程', 'provider_id': 'local', 'cancel_supported': True}

    def _local_artifacts(self, files, task_id):
        artifacts = []
        for file in files:
            if not isinstance(file, dict) or not file.get('filename') or file.get('type', 'output') != 'output':
                continue
            filename, subfolder = file['filename'], file.get('subfolder', '')
            if not self.s.user_can_access_view_item(self.user, filename, subfolder, 'output', task_id):
                continue
            artifacts.append({'name': Path(filename).name, 'url': '/api/view?' + urlencode({
                'filename': filename, 'subfolder': subfolder, 'type': 'output', 'promptId': task_id})})
        return artifacts[:100]

    def _direct_local_result(self, task_id, *, owned_voice_run=None):
        # Match /api/history/detail's durable ownership source. A stale index
        # row must not authorize a sidecar belonging to another owner.
        direct = self.s.direct_sidecar_for_prompt(task_id)
        if direct:
            owner_email = self.s.direct_sidecar_owner_email(direct[0], direct[1], task_id)
            metadata = {'submittedBy': {'email': owner_email}} if owner_email else {}
            files_override = None
            legacy_voice_owned = False
            if owned_voice_run is not None and isinstance(owned_voice_run.get('input'), dict):
                # Voice CLIs write a sidecar while generating in a temporary
                # directory, then the orchestrator copies both files. Its
                # serialized subfolder is stale: use the exact final paths in
                # this owner-validated, immutable run instead.
                payload = owned_voice_run['input']
                root = self.s.COMFY_OUTPUT.resolve()
                sidecar = Path(direct[0]).resolve()
                expected_sidecar = Path(str(payload.get('sidecar_path') or '')).resolve()
                output = Path(str(payload.get('output_path') or '')).resolve()
                if (sidecar != expected_sidecar or not sidecar.is_relative_to(root)
                        or not output.is_relative_to(root) or sidecar != Path(str(output) + '.castle.json')
                        or direct[1].get('promptId') != task_id):
                    raise PermissionError('語音成果與原任務的輸出綁定不一致')
                relative = output.relative_to(root)
                files_override = [{'filename': relative.name,
                    'subfolder': '' if relative.parent == Path('.') else relative.parent.as_posix(), 'type': 'output'}]
                # Existing unsigned CLI outputs have no embedded owner. The
                # already verified exact run plus existing job attribution is
                # their legacy authority; never override a signed/other owner.
                legacy_voice_owned = (not owner_email and 'provenance' not in direct[1]
                    and self.s.user_can_access_job(self.user, task_id))
            if not legacy_voice_owned and not self.s.user_can_access_job(self.user, task_id, metadata, {}):
                raise PermissionError('找不到這個任務')
            detail = self.s.direct_history_detail(task_id, direct[1])
            if detail:
                status = detail.get('status')
                state = 'completed' if detail.get('completed') else {'error': 'failed', 'failed': 'failed', 'cancelled': 'cancelled'}.get(status, 'running')
                files = files_override if files_override is not None else detail.get('outputs') or []
                artifacts = self._local_artifacts(files, task_id) if state == 'completed' else []
                if state == 'completed' and not artifacts:
                    state = 'collecting'
                return {'state': state, 'downstream_id': task_id,
                        'artifacts': artifacts}
        return None

    def _owned_orchestrator_run(self, task_id):
        if not self.s.user_can_access_job(self.user, task_id):
            if self._direct_local_result(task_id) is None:
                raise PermissionError('找不到這個任務')
        run = self.s.castle_orchestrator.get_run_api(task_id).get('run')
        if (not isinstance(run, dict) or run.get('id') != task_id
                or run.get('workflow_type') not in {'castle.voice_speak_guarded', 'castle.voxcpm_tts'}):
            raise PermissionError('這個任務不屬於共用語音生成流程')
        return run

    def _orchestrator_local_result(self, task_id):
        run = self._owned_orchestrator_run(task_id)
        state = {'pending': 'queued', 'running': 'running', 'cancel_requested': 'cancelling',
                 'completed': 'collecting', 'failed': 'failed', 'cancelled': 'cancelled'}.get(
                     run.get('status'), 'submission_uncertain')
        if state == 'collecting':
            direct = self._direct_local_result(task_id, owned_voice_run=run)
            if direct:
                return direct
        return {'state': state, 'downstream_id': task_id}

    def status_local(self, owner, task_id):
        # These voice jobs run in the durable local orchestrator, never in
        # Comfy's queue. Query/cancel its exact run only after owner validation.
        if task_id.startswith('run_'):
            return self._orchestrator_local_result(task_id)
        local = next((x for x in self.s.local_job_snapshot() if x.get('promptId') == task_id), None)
        if local:
            if not self.s.user_can_access_job(self.user, task_id, local):
                raise PermissionError('找不到這個任務')
            state = {'pending': 'queued', 'completed': 'collecting', 'error': 'failed',
                     'cancel_requested': 'cancelling'}.get(local.get('state'), local.get('state'))
            if state == 'collecting':
                direct = self._direct_local_result(task_id)
                if direct:
                    return direct
            # H3/identity wrappers finish verification after Comfy does. Raw
            # history must never override their running/error/cancelled state.
            return {'state': state, 'downstream_id': task_id}
        if task_id.startswith('local-'):
            direct = self._direct_local_result(task_id)
            if direct:
                return direct
            if not self.s.user_can_access_job(self.user, task_id):
                raise PermissionError('找不到這個任務')
            return {'state': 'submission_uncertain', 'downstream_id': task_id}
        # The wrapper may have left the in-memory queue after restart/expiry;
        # its durable attribution still requires a verified direct result.
        index_loader = getattr(self.s, 'load_job_index', None)
        indexed = index_loader().get(task_id, {}) if index_loader else {}
        if indexed.get('jobKind') in {'minimax_h3_web', 'clipmode_character_photoshoot_web'}:
            direct = self._direct_local_result(task_id)
            if direct:
                return direct
            if not self.s.user_can_access_job(self.user, task_id):
                raise PermissionError('找不到這個任務')
            return {'state': 'submission_uncertain', 'downstream_id': task_id}
        history = self.s.request_json('/history/' + quote(task_id, safe=''))
        item = history.get(task_id)
        if item:
            if not self.s.user_can_access_job(self.user, task_id, item):
                raise PermissionError('找不到這個任務')
            status = item.get('status') or {}
            state = 'failed' if status.get('status_str') == 'error' else ('completed' if status.get('completed') else 'running')
            files = [file for output in (item.get('outputs') or {}).values()
                     for key in ('images', 'gifs', 'audio', 'video', 'videos') for file in (output.get(key) or [])]
            artifacts = self._local_artifacts(files, task_id) if state == 'completed' else []
            if state == 'completed' and files and not artifacts:
                state = 'collecting'
            return {'state': state, 'downstream_id': task_id, 'artifacts': artifacts}
        direct = self._direct_local_result(task_id)
        if direct:
            return direct
        if not self.s.user_can_access_job(self.user, task_id):
            raise PermissionError('找不到這個任務')
        queue = self.s.request_json('/queue')
        running = any(x[1] == task_id for x in queue.get('queue_running', []))
        return {'state': 'running' if running else 'queued', 'downstream_id': task_id}

    def cancel_local(self, owner, task_id):
        if task_id.startswith('run_'):
            run = self._owned_orchestrator_run(task_id)
            if run.get('status') not in {'completed', 'failed', 'cancelled'}:
                self.s.castle_orchestrator.cancel_run_api(task_id)
            return self._orchestrator_local_result(task_id)
        result = self.h._cancel_job_result(task_id)
        action = result.get('action')
        if action in {'cancelled', 'deleted'}:
            return {'state': 'cancelled', 'downstream_id': task_id}
        if action in {'cancel_requested', 'interrupted'}:
            return {'state': 'cancelling', 'downstream_id': task_id}
        return self.status_local(owner, task_id)

    def submit_colab(self, owner, request_id, request, limits):
        _, accounts = self._colab_accounts(limits)
        if not accounts:
            raise ComputeRejected('沒有可用且額度足夠的 Colab 帳號', code='colab_account_unavailable')
        bound_limits = {**limits, 'account_id': accounts[0]['id']}
        job = self._run(request, 'colab', request_id, bound_limits)
        return self._colab_receipt(job)

    @staticmethod
    def _colab_receipt(job):
        phase = job.get('phase')
        state = phase if phase in {'queued', 'completed', 'failed', 'cancelled'} else 'running'
        if job.get('cancel_requested') and state not in {'completed', 'failed', 'cancelled'}: state = 'cancelling'
        return {'state': state, 'downstream_id': job['id'], 'provider_id': 'colab',
                'recipe_id': colab_adapter.RECIPE_ID, 'recipe_label': colab_adapter.RECIPE_LABEL,
                'artifacts': job.get('artifacts') or [], 'cancel_supported': True}

    def status_colab(self, owner, task_id):
        return self._colab_receipt(self.colab.public_job(self.colab.job(owner, task_id)))

    def recover_colab(self, owner, request_id):
        try: return self.status_colab(owner, request_id)
        except ValueError: return None

    def cancel_colab(self, owner, task_id):
        return self._colab_receipt(self.colab.cancel(owner, task_id))

    @staticmethod
    def _is_autoip_media(request):
        return request.get('featureId') in {'img2img', 'qwen21-edit', 'img2video', 'h3-image-video', 'first-last-frame'}

    def _autoip_inputs(self, request):
        self._feature(request)
        if request.get('characterId') or request.get('action') or request.get('variantId'):
            raise ComputeRejected('角色工作仍須使用支援角色綁定的執行方式', code='canonical_binding_required')
        from compute_autoip import normalise_autoip_video_intent
        settings, warnings = normalise_autoip_video_intent(
            request['featureId'], request.get('settings') or {}, request.get('computeAutoipVideo'))
        visual = request.get('computeVisualClass') or settings.get('visualClass') or settings.get('visualStyle')
        return settings, visual, warnings

    def _autoip_plan(self, request, limits):
        from compute_autoip import build_autoip_plan
        settings, visual, _ = self._autoip_inputs(request)
        if visual:
            settings = {**settings, 'visualClass': visual}
        if request.get('files'):
            raise ComputeRejected('文字生成不能附帶圖片', code='unsupported_inputs')
        return build_autoip_plan(request['featureId'], settings, max_credits=limits.get('max_credits', 100))

    def inspect_autoip(self, owner, request, limits):
        try:
            settings, visual, conversion_warnings = self._autoip_inputs(request)
            runtime, actor = self.h._provider_runtime_actor()
            if self._is_autoip_media(request):
                from compute_autoip_media import inspect_autoip_media
                candidate = inspect_autoip_media(runtime, actor, request['featureId'], settings,
                    list(request.get('files') or {}), visual_class=visual, max_credits=limits.get('max_credits', 100))
                candidate['warnings'] = conversion_warnings + candidate.get('warnings', [])
                return candidate
            plan = self._autoip_plan(request, limits)
            planned = runtime.service.plan_intent(actor, plan)
            cost = float(planned['quote']['credits'])
            from compute_autoip import normalise_autoip_intent
            _, warnings = normalise_autoip_intent(request['featureId'], settings)
            return {'eligible': True, 'reasons': [], 'queue_depth': 0, 'estimated_cost': cost,
                    'currency': 'credits', 'warnings': conversion_warnings + warnings}
        except ComputeRoutingError as error:
            return {'eligible': False, 'reasons': [error.code], 'queue_depth': 0, 'currency': 'credits'}
        except Exception:
            return {'eligible': False, 'reasons': ['autoip_operation_unavailable'], 'queue_depth': 0, 'currency': 'credits'}

    def _prepare_autoip_media(self, runtime, actor, request_id, request, limits):
        from compute_autoip_media import prepare_autoip_media
        settings, visual, _ = self._autoip_inputs(request)
        fresh, paths = [], {}
        try:
            # Authenticate every reused input before reading or staging bytes.
            files = self.s.validate_client_generation_files(request.get('files'), self.user)
            files = self.s.validate_generation_file_contract(self._feature(request), files)
            for slot, item in files.items():
                if not item:
                    continue
                if item.get('serverFile'):
                    filename = self.s.resolve_server_file(item['serverFile'])
                else:
                    filename = self.s.stage_colab_uploaded_file(item, slot)
                    fresh.append(self.s.COMFY_INPUT / filename)
                paths[slot] = (self.s.COMFY_INPUT / filename).resolve()
            authorized = frozenset(paths.values())
            return prepare_autoip_media(runtime, actor, request['featureId'], settings, paths,
                request_id=request_id, visual_class=visual,
                allowed_source_roots=(self.s.COMFY_INPUT,),
                staging_root=runtime.config.database_path.parent / 'autoip-outputs' / 'compute-inputs',
                authorize_source=lambda user_id, path: user_id == actor.user_id and path in authorized,
                max_credits=limits.get('max_credits', 100))
        except ComputeRoutingError:
            raise
        except (ValueError, PermissionError, KeyError, OSError):
            raise ComputeRejected('圖片或設定未通過檢查，沒有提交生成。', code='input_validation_failed') from None
        finally:
            for path in fresh:
                path.unlink(missing_ok=True)

    def submit_autoip(self, owner, request_id, request, limits):
        from provider_fabric.service import ProviderServiceError
        runtime, actor = self.h._provider_runtime_actor()
        plan = (self._prepare_autoip_media(runtime, actor, request_id, request, limits)
                if self._is_autoip_media(request) else self._autoip_plan(request, limits))
        try:
            result = runtime.service.submit_generation(actor, {'schema_version': 1,
                'request_id': 'generation:' + request_id.replace('-', ''), 'plan': plan})
        except ProviderServiceError as error:
            if error.code == 'spend_limit_exceeded':
                raise ComputeRejected('即時報價超過點數上限，沒有提交生成。', code='budget_exceeded') from None
            raise
        return {'state': 'queued', 'downstream_id': result['intent_id'], 'status_url': result['status_path'],
                'provider_id': 'autoip', 'recipe_id': 'autoip:' + plan['capability'],
                'recipe_label': 'AutoIP 服務配方', 'cancel_supported': False}

    def status_autoip(self, owner, task_id):
        runtime, actor = self.h._provider_runtime_actor()
        result = runtime.service.get_intent_status(actor, task_id)
        state = {'pending': 'queued', 'preparing': 'queued', 'submitted': 'queued',
                 'queued': 'queued', 'running': 'running', 'completed': 'completed',
                 'failed': 'failed', 'cancelled': 'cancelled', 'timeout': 'submission_uncertain',
                 'abandoned': 'rejected', 'submission_uncertain': 'submission_uncertain'}.get(
                     result.get('state'), 'submission_uncertain')
        receipt = {'state': state, 'downstream_id': task_id,
                   'status_url': '/api/intents/' + quote(task_id, safe=':') + '/status'}
        if state == 'completed':
            # A provider completion precedes our owned, verified download. Keep
            # polling until adoption so a terminal receipt cannot lose outputs.
            output_ids = runtime.store.list_owned_intent_output_ids(actor.user_id, task_id) if result.get('output_verified') else []
            receipt['state'] = 'completed' if output_ids else 'collecting'
            receipt['artifacts'] = [{'id': asset_id, 'name': '成果 ' + str(index + 1),
                                     'url': '/api/assets/' + quote(asset_id, safe=':') + '/source'}
                                    for index, asset_id in enumerate(output_ids)]
        return receipt

    def recover_autoip(self, owner, request_id):
        runtime, actor = self.h._provider_runtime_actor()
        result = runtime.service.recover_generation_request(actor, 'generation:' + request_id.replace('-', ''))
        if result is None:
            return None
        return self.status_autoip(owner, result['intent_id'])

    def _placement(self, body):
        if 'placement' in body:
            return body['placement']
        return self.preferences.placement(self.owner, (body.get('request') or {}).get('featureId'))

    def submit(self, body):
        # A replay without explicit placement keeps the original decision,
        # even if defaults changed since the first submission.
        placement = self._placement(body)
        if 'placement' not in body:
            try:
                previous = self.router.status(self.owner, body.get('request_id'), refresh=False)
                placement = previous['placement']
            except ComputeRoutingError as error:
                if error.code != 'not_found':
                    raise
        return self.router.submit(self.owner, body.get('request_id'), body.get('request'), placement, body.get('limits'))

    def preview(self, body):
        request = body.get('request') or {}
        limits = body.get('limits') or {}
        result = self.router.preview(self.owner, request, self._placement(body), limits)
        snapshot = self.colab.snapshot(self.owner)
        result['accounts'] = [{k: a.get(k) for k in ('id','label','authenticated','balance_compute_units')} for a in snapshot['accounts']]
        result['recipes'] = {'local':'本機既有工作流程', 'colab':colab_adapter.RECIPE_LABEL, 'autoip':'AutoIP 服務配方',
                             'comfy_cloud': 'Comfy Cloud 已部署工作流', 'runninghub': 'RunningHub 已發布工作流'}
        try:
            result['colab_compatibility'], result['colab_warnings'] = self._colab_plan(request)
        except (ValueError, PermissionError, KeyError): pass
        try:
            from compute_autoip import normalise_autoip_intent
            settings, _, conversion_warnings = self._autoip_inputs(request)
            _, warnings = normalise_autoip_intent(request['featureId'], settings)
            result['autoip_warnings'] = conversion_warnings + warnings
        except (ValueError, KeyError): pass
        return result
