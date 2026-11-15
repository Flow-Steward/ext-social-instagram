# Instagram for Flow Steward

`flowsteward.instagram` 0.1.0 is an independent, public-SDK-only `tool_provider` for Instagram professional accounts using Instagram Login and `graph.instagram.com`. It targets `extension_host_v2`, extension API 1.9.0 and SDK 0.5.0+. The application release is 0.9.18; these version numbers describe separate contracts.

**Preparation checkpoint:** declarations and behavior tests exist; provider runtime deliberately returns `not_implemented` until the coordinator observes the first behavioral RED in Docker. This checkpoint is not a working release or a publishable bundle.

## Connection setup

1. Create a Meta app with the Instagram API / Instagram Login product and authorize the professional Instagram account. Use the account's Instagram Login user access token. This extension does not exchange or refresh tokens; replace expired credentials through the connection form.
2. Request `instagram_business_basic`, `instagram_business_content_publish` for publishing and `instagram_business_manage_comments` for comment reads/replies. Complete App Review / Advanced Access where Meta requires access to accounts outside your app's own managed test accounts. See the [Instagram Login publishing guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing) and [media permissions reference](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/v21.0/).
3. Create one project connection per account. Set `ig_user_id`, `app_id` and an explicitly supported `graph_api_version` (offline fixtures use `v27.0`). Store `access_token` and `app_secret` as secrets, never in workflow inputs. Verify connection performs a provider identity read and must match the configured account.
4. Open the project **Webhooks** page and select a saved Instagram connection as the intake anchor. Use its manual callback setup to create the registration (`configure: false`), then copy the one-time HTTPS callback URL and verify token into the Meta app Webhooks configuration. The extension does not expose a `configure_webhook` action. Select Instagram and subscribe the `comments` field; enable the account subscription required by Meta. The host answers the exact `hub.mode=subscribe` challenge and checks the original request bytes against `X-Hub-Signature-256` using this connection's app secret. Generic signed callbacks require host API 1.9.0.

The **Connections** page has no connection selector requirement, so the first account can be created when none exist. The **Overview** page is also available with no account and reports unconfigured readiness. Only **Webhooks** requires a selected saved `instagram_account`; its selector provides the anchor ID for manual setup.

## Operations

| Operation | Inputs | Result |
| --- | --- | --- |
| `publish_image` | public HTTPS `image_url`, `caption`, optional `alt_text` | container ID, pending/published status; media ID and permalink when known |
| `publish_carousel` | public HTTPS `image_urls`, `caption` | child IDs, parent container ID, status, media ID/permalink when known |
| `get_publish_status` | `container_id` | read-only normalized status and original provider status |
| `publish_container` | `container_id` | explicit finalization of an existing, host-recorded container |
| `list_posts` | optional `limit`, `cursor` | bounded items and next cursor |
| `get_post` | `media_id` | owned post |
| `list_comments` | `media_id`, optional `limit`, `cursor` | owned post's comments |
| `list_replies` | `comment_id`, optional `limit`, `cursor` | replies after comment/media ownership checks |
| `reply_to_comment` | `comment_id`, `text` | reply ID after ownership checks |

Reels, Stories, video, direct messages and ads are outside this release. The extension calls only `graph.instagram.com`; it does not fetch or transform supplied images. Each image must already be a publicly reachable HTTPS JPEG. File size must be at most 8 MB and aspect ratio between 4:5 and 1.91:1. The provider enforces actual image bytes, dimensions and content; local syntax/public-address checks do not certify those properties. Private Flow Steward artifact grants do not make an image publicly accessible.

Captions accept at most 2200 characters without truncation. Optional alt text is at most 1000 characters. Image carousels accept 2–20 images, create every child first, then create and finalize a parent. A failed child prevents an incomplete album from being published and returns any already-created child IDs. Limits follow the [current IG User `/media` reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media), checked 2026-11-14. Its current 20-child definition takes precedence here over the older Instagram Login guide's 10-item statement. URL filenames do not prove JPEG content.

## Graph request contract

GET inputs use query parameters. POST inputs use `application/x-www-form-urlencoded` bodies built with standard URL encoding; structured values such as the carousel `children` array are JSON-encoded inside a single form field. Tokens remain in the Bearer authorization header and image URLs stay out of POST request URLs. This matches the [official Meta Business SDK request serialization](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py), checked 2026-11-14; the SDK itself is not a dependency.

Runtime implementation must use the public Flow Steward SDK `open_pinned_url(Request(...), timeout_seconds=...)`, including its peer pinning and default redirect refusal. The Graph origin remains fixed; a provider `paging.next` URL is never executed and only the opaque `after` cursor is reused. A stubbed302 response proves the extension's boundary response only; the public SDK owns actual HTTP redirect/SSRF enforcement.

## Pending and uncertain publishing

Calls are bounded; there is no extension worker, background polling or blind write retry. Container creation, readiness check, account-scoped `media_publish`, and permalink retrieval are separate phases. `IN_PROGRESS` maps to pending; `FINISHED` is ready; `ERROR` is failed; `EXPIRED` is expired; `PUBLISHED` is published. See the [IG Container reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-container).

Keep the returned `container_id`. `get_publish_status` only reads. Resume with `publish_container(container_id)` on the same connection: the container must have a host-recorded receipt for this connection/account, status is checked first, and only `FINISHED` can reach the configured account's `media_publish` endpoint. Already-published containers are never resent. The API does not expose an owner field on containers, so recovery uses the scoped receipt rather than inventing provider ownership evidence.

The extension declares non-secret project state `publishing/containers`. Writes preserve existing receipts, omit `connection_id` so the host binds the current runtime connection (or echo that exact same ID), and echo the read `expected_revision`; the host's compare-and-swap rejects stale updates. A conflict requires reading current state and reconciling, never dropping the revision. Receipts hold IDs/status only, not tokens, image URLs or captions. State persistence is performed by the host after the response and is not atomic with the remote publish: the host must preserve an uncertain outcome if persistence fails after a write. A crash before returning a receipt can leave an unknown provider container; do not recreate on that basis.

An ambiguous create/publish timeout returns `external_effect_status: timeout_unknown` and never `definitely_no_external_effect: true`. Known container IDs remain available for read-first recovery. Only proven preflight failures or a first definitive provider refusal can claim no external effect. A successful publish response containing the media ID remains published even if the later permalink lookup fails. Never use a missing permalink as a reason to resend.

All four write operations declare host idempotency required and Test Run suppression. The host's external-effect ledger guards workflow and direct action paths; runtime tests alone do not prove host replay or Test Run behavior. Reads are not declared external effects.

## Comment workflow triggers

`instagram.comment.created` emits one event per new root comment by another person. Signed provider `entry.id` must belong to `webhook_account_ids`; omitted allowlist means only `ig_user_id`, and an explicit empty list disables intake. Choose one anchor per Meta app and explicitly list the app accounts you intend to receive. Intake stays on the host-authenticated anchor connection and project; a payload cannot remap it to a different connection. Outbound actions still select their own connection.

The ingress handler performs no network requests and uses the signed provider entry and media/comment relationship as provenance. It ignores foreign-account entries, own authors (including other allowlisted accounts), replies, irrelevant fields, ads and unsupported products. A root comment payload contains `provider`, `project_id`, `connection_id`, `ig_user_id`, `media_id`, `comment_id`, `author_id`, `text`, `event_timestamp`, `action: created` and `dedupe_key`. `event_timestamp` is signed `entry.time`, a delivery timestamp, not a claimed comment creation timestamp. Missing provider creation timestamps remain absent. See the [Instagram webhook reference](https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram).

Top-level `source_id` and `correlation_id` equal the stable dedupe key, namespaced by extension, anchor connection, Instagram account, media, comment and the created action. Delivery timestamp and comment text do not change identity. The existing host durable `provider_event_trigger_dispatch` ledger owns replay suppression; separate subprocess invocations may emit the same key. Trigger filters are `ig_user_id` and `media_id`, applied without changing the callback anchor.

## Offline authoring verification

Run from the target Flow Steward source checkout; do not use host pytest. The supplied SDK 0.5.0 universal wheel is a development dependency; the host supplies the same public SDK namespace at runtime. No private Core or sibling-extension imports are permitted.

First RED (coordinator owns the shared Docker gate slot):

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-tests --extension-root /absolute/path/to/flowsteward_instagram --test-paths tests/test_publishing.py::test_publish_image_creates_checks_readiness_publishes_and_reads_permalink
```

After observed behavioral RED and runtime implementation, run scoped extension static and test checks, then use the public extension validate/test/bundle lifecycle. No validation, RED, GREEN, bundle, installation or publication is claimed at this preparation checkpoint. The current callback profile also depends on the coordinated Core signed-callback change.

`BRANDING.md` records the unchanged official Instagram icon. `PRIVACY.md` describes provider transfers and retention.
