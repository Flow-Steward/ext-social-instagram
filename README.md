# Instagram for Flow Steward

Publish photos and image carousels to an Instagram professional account from Flow
Steward workflows. Read owned posts, comments and replies, respond to audience
questions, and start a workflow from new root comments. Prepare captions and images
in earlier steps and use publishing status/recovery actions when Meta returns a
pending result.

`flowsteward.instagram` 0.1.1 is an independent `tool_provider` using Instagram Login
and `graph.instagram.com`. It requires Flow Steward 0.9.18 or later with
`extension_host_v2`, extension runtime API 1.9.0 and public SDK 0.5.0 or later.
Application, runtime API and SDK versions describe separate contracts. Automated
checks use fake provider responses; no live account or production publishing
acceptance is claimed.

## Connection setup

1. Create a Meta app with the Instagram API / Instagram Login product and authorize the professional Instagram account. Use the account's Instagram Login user access token. This extension does not exchange or refresh tokens; replace expired credentials through the connection form.
2. Request `instagram_business_basic`, `instagram_business_content_publish` for publishing and `instagram_business_manage_comments` for comment reads/replies. Complete App Review / Advanced Access where Meta requires access to accounts outside your app's own managed test accounts. See the [Instagram Login publishing guide](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing) and [media permissions reference](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/v21.0/).
3. Create one project connection per account. Set `ig_user_id`, `app_id` and an explicitly supported `graph_api_version` (offline fixtures use `v27.0`). Store `access_token` as a secret, never in workflow inputs. `app_secret` is optional for publishing/reads, and required for receiving signed comments. Verify connection performs a provider identity read and must match the configured account.
4. Open the project **Webhooks** page and select a saved Instagram connection as the intake anchor. Use its manual callback setup to create the registration (`configure: false`), then copy the one-time HTTPS callback URL and verify token into the Meta app Webhooks configuration. The extension does not expose a `configure_webhook` action. Select Instagram and subscribe the `comments` field; enable the account subscription required by Meta. The host answers the exact `hub.mode=subscribe` challenge and checks the original request bytes against `X-Hub-Signature-256` using this connection's app secret. Generic signed callbacks require host API 1.9.0.

The **Connections** page has no connection selector requirement, so the first account can be created when none exist. The **Overview** page remains accessible without an account and explains that readiness requires a selection; it does not inspect saved credentials or imply that a saved account is unconfigured. **Webhooks** selects a saved `instagram_account` for both the readiness panel and manual callback setup. Its connection-scoped query receives the host-resolved selected connection, so an outbound-only account is ready for publishing even without a webhook signing secret; webhook readiness is reported separately. The ungated overview never fetches project-global provider secrets.

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

Media reads request conservative Instagram Login fields: ID, media type/URL, permalink, timestamp, username and ownership. They omit `caption` and `media_product_type`, marked Facebook-Login-only read fields in the [current Media reference](https://developers.facebook.com/docs/instagram-platform/reference/instagram-media/v21.0/). Publishing caption input remains supported. Comment/reply access proves the comment's media relationship and that media's `owner.id`; missing ownership fails closed.

Captions accept at most 2200 characters without truncation. Optional alt text is at most 1000 characters. Image carousels accept 2–20 images, create every child first, then create and finalize a parent. A failed child prevents an incomplete album from being published and returns any already-created child IDs. Limits follow the [current IG User `/media` reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media), checked 2026-11-14. Its current 20-child definition takes precedence here over the older Instagram Login guide's 10-item statement. URL filenames do not prove JPEG content.

## Graph request contract

GET inputs use query parameters. POST inputs use `application/x-www-form-urlencoded` bodies built with standard URL encoding; structured values such as the carousel `children` array are JSON-encoded inside a single form field. Tokens remain in the Bearer authorization header and image URLs stay out of POST request URLs. This matches the [official Meta Business SDK request serialization](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/api.py), checked 2026-11-14; the SDK itself is not a dependency.

The runtime uses the public Flow Steward SDK `open_pinned_url(Request(...), timeout_seconds=...)`, including its peer pinning and default redirect refusal. The Graph origin remains fixed; a provider `paging.next` URL is never executed and only the opaque `after` cursor is reused. Responses are capped at 2 MiB and closed. Each exchange has a total interrupting deadline of at most10seconds covering opener acquisition, headers and body reads; all phases share a25-second monotonic operation budget, reserving5seconds before the default30-second host subprocess limit for a pending receipt/envelope. The main-thread POSIX timer is restored on normal/HTTP-error/failure paths, including a caller's elapsed countdown and interval. Unsupported threading/platform contexts fail before provider I/O; no uncancellable reader worker is created. This is not a hard real-time operating-system scheduling guarantee. Fake-clock SDK-opener tests charge acquisition and blocking body waits separately; the public SDK still owns actual HTTP redirect/SSRF enforcement.

## Pending and uncertain publishing

Calls are bounded; there is no extension worker, background polling or blind write retry. Container creation, readiness check, account-scoped `media_publish`, and permalink retrieval are separate phases. `IN_PROGRESS` maps to pending; `FINISHED` is ready; `ERROR` is failed; `EXPIRED` is expired; `PUBLISHED` is published. See the [IG Container reference](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-container).

Keep the returned `container_id`. `get_publish_status` only reads. Resume with `publish_container(container_id)` on the same connection: the container must have a host-recorded receipt for this connection/account, status is checked first, and only `FINISHED` can reach the configured account's `media_publish` endpoint. Already-published containers are never resent. The API does not expose an owner field on containers, so recovery uses the scoped receipt rather than inventing provider ownership evidence.

The extension stores one non-secret receipt row per parent: namespace `publishing`, key `container:<container_id>`. Recovery uses the host's exact input-derived state lookup, so an older receipt remains accessible regardless of unrelated receipt count. Every update echoes the current runtime connection ID; an existing row also echoes its positive `expected_revision`. The host's compare-and-swap rejects stale updates. A conflict requires reading current state and reconciling, never dropping the revision. Recovery rejects project-wide fallback rows, foreign connections/accounts, mismatched keys/containers, and missing receipts before provider I/O.

Distinct provider parent IDs use distinct state keys, preserving unrelated first-publish receipts without merging an aggregate row. The host's first-write API is an upsert, so this is not a create-if-absent guarantee for simultaneous writes to the same container. Existing-row CAS and host effect/idempotency controls still apply. Receipts hold IDs/status only, not tokens, image URLs or captions. State persistence happens after the response and is not atomic with the remote publish: the host preserves an uncertain outcome if persistence fails after a write. A crash before returning a receipt can leave an unknown provider container; do not recreate on that basis. This unreleased package has no legacy aggregate receipt migration.

Under Flow Steward's conservative write policy, HTTP408 after a POST remains an unknown effect even when its response includes a Graph error code.

An ambiguous create/publish timeout returns `external_effect_status: timeout_unknown` and never `definitely_no_external_effect: true`. Newly created known parents return a handled `ok:true`, pending result with receipt updates and explicit unknown metadata when readiness/finalization fails. This allows the host to process the receipt before effect confirmation blocks unsafe replay; it is not a claim that a post was published. Existing durable receipts can return a failed unknown envelope for explicit read-first recovery. A partial carousel with only child IDs returns those IDs and never publishes an incomplete album; no resumable parent exists yet. Only proven preflight failures or a first definitive provider refusal can claim no external effect. Malformed provider errors or exceptions after a POST preserve uncertainty. A successful publish response containing the media ID remains published even if the later permalink lookup fails. Never use a missing permalink as a reason to resend.

All four write operations declare host idempotency required and Test Run suppression. The host's external-effect ledger guards workflow and direct action paths; runtime tests alone do not prove host replay or Test Run behavior. Reads are not declared external effects.

## Comment workflow triggers

`instagram.comment.created` emits one event per new root comment by another person. Signed provider `entry.id` must belong to `webhook_account_ids`; omitted allowlist means only `ig_user_id`, and an explicit empty list disables intake. Choose one anchor per Meta app and explicitly list the app accounts you intend to receive. Intake stays on the host-authenticated anchor connection and project; a payload cannot remap it to a different connection. Outbound actions still select their own connection.

The ingress handler performs no network requests and uses the signed provider entry and media/comment relationship as provenance. It ignores foreign-account entries, own authors (configured anchor plus allowlisted accounts, even if intake excludes the anchor), replies, irrelevant fields, ads and unsupported products. The provider documents `from.self_ig_scoped_id` as well as an Instagram-scoped `from.id`; those IDs need not be interchangeable with the configured account ID. Our conservative inference suppresses signed comments carrying a nonempty self marker. This is an extension policy, not a verified guarantee about every live payload's marker semantics; a foreign author without the marker remains eligible. A root comment payload contains `provider`, `project_id`, `connection_id`, `ig_user_id`, `media_id`, `comment_id`, `author_id`, `text`, `event_timestamp`, `action: created` and `dedupe_key`. `event_timestamp` is signed `entry.time`, a delivery timestamp, not a claimed comment creation timestamp. Missing provider creation timestamps remain absent. See the [Instagram webhook reference](https://developers.facebook.com/docs/graph-api/webhooks/reference/instagram).

Top-level `source_id` and `correlation_id` equal the stable dedupe key, namespaced by extension, anchor connection, Instagram account, media, comment and the created action. Delivery timestamp and comment text do not change identity. The existing host durable `provider_event_trigger_dispatch` ledger owns replay suppression; separate subprocess invocations may emit the same key. Trigger filters are `ig_user_id` and `media_id`, applied without changing the callback anchor.

## Offline authoring verification

Run from the target Flow Steward source checkout; do not use host pytest. The supplied SDK 0.5.0 universal wheel is a development dependency; the host supplies the same public SDK namespace at runtime. Shipped runtime imports only public SDK/stdlib/local package modules. A separate `tests/test_host_contracts.py` opts into read-only actual host consumers only in managed `fs-audit` with `/app/core`; it uses fake persistence/queue boundaries and no real database, credentials or provider. It characterizes event routing/replay and receipt/effect processing, not SQL atomicity or durable storage.

Run focused declaration checks from a compatible Flow Steward source checkout,
through its coordinated managed Docker audit environment:

```sh
./core/entrypoints/cli/scripts/lint.sh --checks extension-static,extension-tests --extension-root /absolute/path/to/flowsteward_instagram --test-paths tests/test_contracts.py
```

The repository CI runs standalone credential-free unit tests and packages a ZIP
for catalog validation. Host-consumer tests skip when the Flow Steward Core is
absent; they do not turn standalone CI into a host integration test. Provider
permissions, account subscriptions, actual media processing and live publishing
must be checked with the intended account before production use. Signed comment
callbacks require runtime API 1.9.0 and the configured app secret.

`BRANDING.md` records the unchanged official Instagram icon. `PRIVACY.md` describes provider transfers and retention.

## Release packaging

The agreed publisher repository is `Flow-Steward/ext-social-instagram`. Its creation,
initial push and first release are coordinated with the Core release owner;
these source files do not claim that a public release already exists.

The prepared CI workflow runs the standalone tests, builds the archive from
tracked source with `.github/scripts/package_extension.py`, and checks it with
the catalog checker. In CI, an unavailable catalog checker fails the job; a
manual local offline preview still reports that catalog validation was not
performed. The release verifies the CI artifact checksums before attaching the
ZIP and SHA256SUMS to the release. Packaging excludes `.github/`, development wheels and
caches. The release workflow checks that `v<version>` matches `extension.yaml`,
runs CI and publishes the checked artifact. Create/push a release tag only after
the agreed Core release acceptance and the publisher's final catalog review.
Catalog registration is a separate owner-managed change; never edit generated
catalog indexes or replace an already published version's ZIP.

## Support

Open the extension's **Support** page in Flow Steward to ask a question, report a
problem or request a feature. You can also [create a GitHub issue](https://github.com/Flow-Steward/ext-social-instagram/issues/new/choose)
or [browse existing issues](https://github.com/Flow-Steward/ext-social-instagram/issues).
Issues are public; never include passwords, API keys, tokens, connection strings
or customer data.
