# AutoIP integration

The provider adapter implements seven media-generation operations. Its contract
is based on `@autoip/mcp@0.4.0`, with reviewed amendments from the operator-supplied
**2026-10-22 AutoIP Public API Skill**. That skill was supplied as a local document;
no account key or paid endpoint was used to implement or test this update.

## Connect your own account

Create your own key in the [AutoIP API dashboard](https://autoip.app/dashboard/api-keys).
In the WebUI's provider settings, an administrator uses **Connect AutoIP / 連接
AutoIP**, chooses an account ID, and enters the key. The existing write-only
credential endpoint binds it to the signed-in owner and chosen account:
`PUT /api/providers/autoip/accounts/{account_id}/credential`. It does not return
the key. Connection verification reads account/pricing information; generation
is a separate action charged by the provider.

The server supports environment-backed credential references for custom
integrations, for example `SecretReference(SecretKind.ENV, "AUTOIP_API_KEY")` in
`ProviderConfig`. Set the environment variable in your local service configuration
or a private, ignored environment file. Merely defining it does **not** create a
WebUI account: use the account connection flow or explicitly register an owned
provider account with that reference. Public code contains no shared account or
key. Never place a real key in workflow JSON, a committed example, or a screenshot.

This application's existing durable adapter pins the official
`https://autoip.app/api/v1` origin. The upstream skill also describes an
`AUTOIP_BASE_URL` override; this adapter does not consume that variable. Keep
that distinction when configuring another AutoIP client. External API workflows
in the community catalog remain discoverable and may use their own configurable
endpoints and credentials.

## Implemented operations

| Provider endpoint | Application support |
| --- | --- |
| `POST /images/text-to-image` | Existing standard, hd_4k, v2, v3, anime modes; mode remains bound to visual class |
| `POST /images/image-to-image` | standard, hd_4k, **v3**; original/default or explicit size, strength; creative negative prompt up to 2,000 characters in hd_4k only |
| `POST /videos/text-to-video` | Resolution-aware duration limits below |
| `POST /videos/image-to-video` | First frame, optional last frame; same duration limits |
| `POST /videos/multi-subject-reference` | Existing 1–4 image application limit, optional descriptions/dialogue/voice sample; minimum remains 5 seconds. Upstream now allows 6 images; the application has not expanded its identity-binding limit |
| `POST /videos/lip-sync` | Existing default engine, image/audio inputs. New h3 engine/resolution pricing is not activated |
| `POST /videos/dance` | Uploaded dance-video route; template listing is readable, but mutable template IDs are not exposed as executable UI options |

Optional image `style` values are accepted by the transport contract. The current
WebUI option profiles do not expose a style selector. Image-to-image negative
prompts in standard/v3 are rejected locally rather than silently ignored.

| Operation | 480p | 768p | 2k |
| --- | --- | --- | --- |
| Text/image to video | 2–15 seconds | 2–10 seconds | 5–10 seconds |
| Multi-reference video | 5–15 seconds | 5–10 seconds | 5–10 seconds |

The default remains 5 seconds. Profiles expose the union of duration choices;
the request contract checks the selected resolution/operation combination, and
the quote service requires an exact entry in the current `/pricing` matrix.
No per-second prices are hardcoded or extrapolated. New image-to-image v3 pricing
is recognized when present; an older price snapshot remains readable but cannot
produce a v3 quote. Option profiles are revision `autoip-0.4.0-options-v4`.

## Documented upstream endpoints not yet implemented here

| Endpoint/family | Outstanding application work |
| --- | --- |
| `/images/image-to-prompt` | New request/input contract and text-result task ingestion |
| `/text/post` | Text generation profile and `result_text` handling |
| `/videos/expand-prompt` | Free text-result task flow; not a file download |
| `/images/image-mix` | 2/3-image input contract, profiles and pricing |
| `/images/multi-angle` | 8/17-angle output contract and material ingestion |
| `/videos/character-dance` | animation/replacement request modes and pricing |
| `/videos/keyframe` | 2/3-frame input contract; distinct fixed 6/10/15-second rule |
| `/videos/restore` | Operation list, trims, segment pricing and source validation |
| `/voice/clone` | Voice reference selection and speech-output task flow |
| `/characters`, character images | Remote library CRUD and owner-bound synchronization |
| `/prompts`, `/voices` | Remote library CRUD and prompt/voice linkage |

The provider's image-to-prompt, post, and prompt-expansion tasks return text in
`result_text` (image-to-prompt also aliases it as `result_prompt`) with empty
`outputs`. They must not be routed through the existing media-download worker.
The table is an implementation boundary, not a claim that these provider APIs
are unavailable. Existing `/me`, `/pricing`, balance, task-list/status and
authenticated output-download integration remains in place. Output download URLs
require the same account key and are not public image/video links.

Validation uses synthetic fixtures and no remote credentials:

```sh
pytest-disktmp uv run pytest -q scripts/test_autoip_contract.py scripts/test_autoip_oct22.py scripts/test_provider_autoip_profiles.py scripts/test_autoip_quote.py scripts/test_autoip_worker.py
```
