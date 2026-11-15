# Privacy and data handling

This independent integration sends an account's publishing inputs (public image URLs, captions, alt text and comment replies) to Meta's `graph.instagram.com` using the selected Instagram Login token. Meta retrieves the public images itself. Supplying a private artifact reference does not publish or expose that artifact.

Connections store account/app IDs, an explicit Graph version and optional webhook account allowlist as configuration. Access tokens and app secrets belong in host-encrypted project secret storage and stay write-only in UI. Responses, logs, cursors, errors and state must not expose them. Outbound requests use authorization headers and form-encoded POST bodies, with tokens and image inputs excluded from POST request URLs. The public SDK pinned HTTP transport supplies redirect refusal and peer pinning; requests have bounded timeouts and response sizes, and provider paging URLs are not followed.

Signed callbacks contain account/media/comment IDs, author IDs and comment text. The extension emits only normalized workflow data for explicitly allowed accounts, retaining the authenticated anchor project and connection. Workflow and event records can retain that data under the host's configured retention. Stable event IDs are non-secret dispatch identities; they are not a process-local dedupe store.

Non-secret pending-container receipts contain connection/account/container/media IDs and status, with revision-controlled host storage. They exclude tokens, app secrets, captions, full callback bodies and public image URLs. The host also retains external-effect status and provider IDs to prevent unsafe resend after timeouts. Delete data through the host's connection/project/extension lifecycle and apply your Meta account/app deletion obligations. This package provides no independent analytics, telemetry or background service.

Publishing and comment data is subject to [Meta's Privacy Policy](https://www.facebook.com/privacy/policy/). The configured operator controls the public image server, host storage and credential revocation. Live Meta credentials and live publishing are not needed by the offline tests.
