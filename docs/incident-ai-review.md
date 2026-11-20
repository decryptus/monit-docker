# Incident assistance: implementation and qualification

Development candidate, 20 November 2026. Not a release announcement.
User instructions are in the [English](incident-ai.md) and
[French](incident-ai-fr.md) guides.

## Responsibility and shared-component review

`incident.py` owns evidence selection, freshness, result validation and the
one-request application service. It imports only the standard library and
neutral domain contracts. It has no Docker client, action callback, CLI, HTTP
or display dependency. The CLI is a peer adapter; it exits through the existing
offline-command path without constructing the monitoring engine.

Before implementation, the current DWho notifier/configuration modules,
HTTPdis routes and Sonicprobe helpers were inspected. DWho's notification
transport is not a bounded request/response inference interface; HTTPdis serves
inbound requests; Sonicprobe's YAML/URL helpers do not implement this response
contract. Reuse `dwho.cli.write_json` and `sonicprobe.helpers.load_yaml`.
Declare PycURL directly because this adapter imports it (it was already a
transitive Sonicprobe dependency). libcurl supplies the total transfer deadline,
TLS verification and capped response callbacks; no provider SDK is added.

Evidence rules and prompt policy belong to Monit-Docker. The small protocol
adapter stays here until another actual consumer establishes a shared contract;
no speculative framework or shared-library release is required. Neither the
monitoring runtime nor configuration imports acquire an AI dependency.

Protocol references:

- [Ollama Chat API](https://docs.ollama.com/api/chat)
- [OpenAI Chat Completions](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create)
- [Anthropic Messages](https://platform.claude.com/docs/en/api/messages/create)
- [Anthropic authentication](https://platform.claude.com/docs/en/manage-claude/authentication)
- [Anthropic API versioning](https://platform.claude.com/docs/en/api/versioning)

The Anthropic addition reuses the existing bounded PycURL transport and neutral
service contract. A fresh inspection of the installed DWho, HTTPdis and Sonicprobe
modules found no existing multi-provider inference adapter to reuse. No SDK or
dependency is added. The native adapter maps system/messages, output limits,
completion state and usage into the existing interface; evidence policy stays
in the application service. It uses version `2026-10-21` and the documented bearer
API-key authentication, with single-workspace keys only.

[LiteLLM](https://docs.litellm.ai/docs/) is an existing unified SDK/gateway option,
not a capability to reimplement here. Its optional gateway can be evaluated via
the OpenAI-compatible adapter if the selected deployment supports this candidate's
JSON and finish contract. No LiteLLM deployment or compatibility qualification is
claimed. Keeping the direct Claude adapter avoids a mandatory gateway or a new
SDK dependency for standalone agents; a shared multi-project integration remains
a separate architecture decision.

## Boundaries verified by automated tests

- Exact ID matching; other containers, raw text, names, IDs and secret canaries
  excluded from outgoing context. Preview never loads credentials or calls a model.
- Fresh versus retained/old/future snapshots; unavailable metrics remain missing;
  maintenance, protection and restart limits remain explicit evidence.
- Bounded journal history, closed action enums and normalized supplied rules;
  custom command text is excluded and rule activation is never asserted.
- Real loopback HTTP exchanges with synthetic Ollama/OpenAI-compatible/Anthropic servers;
  one request, protocol fields, header-only key, usage allowlist, incomplete output,
  refusal, tool calls, malformed JSON and unknown evidence IDs.
- Native Anthropic system/message mapping and version header, required private key,
  `end_turn` plus exactly one text block, rejection of thinking/tool content and
  refusal/truncation, bounded numeric usage, and incomplete accounting for cached
  input. The same local JSON/evidence validation and no-action service apply.
- HTTP errors, redirect rejection, response/header caps, total slow-transfer
  deadline, private regular secret files and invalid endpoint/configuration input.
- Direct service invocation with interfaces/adapters blocked; provider failure
  leaves monitoring state and its next collection independent. CLI tests reject
  accidental engine construction. Terminal control sequences remain quoted.

Tests live in the existing `unittest` discovery root. Helpers use a filename
outside `test*`. The installed-wheel and Docker-image CI jobs run the same cases
outside the source checkout. The packaging test exercises the installed `explain`
entry point without a model.

## Remaining qualification before release

### Reproducible synthetic evaluation

From a source checkout with the candidate installed, generate an offline preview:

```sh
python .github/scripts/qualify-incident-ai.py --output /tmp/incident-preview.json
```

This previews eight synthetic cases without loading credentials or contacting a
provider. The report contains the exact redacted contexts, context/prompt/service
and tool hashes, and review expectations. Names, IDs and hostile free-text canaries
must be absent before any request. No Docker daemon, monitoring engine or demo
server is involved. The script is contributor tooling, not an installed CLI command.

Select a provider, endpoint and model using the existing configuration examples.
For a remote provider, use HTTPS with `allow_remote: true`; put its dedicated key
in the configured private regular file, owned by the invoking account with mode
`0600`. Do not put the key in a command, report, repository or chat. Preview does
not verify connectivity, credentials or model compatibility.

Start with one explicitly selected case:

```sh
python .github/scripts/qualify-incident-ai.py --live \
  --ai-config /private/incident-ai.yml --case memory-pressure --max-calls 1 \
  --output /tmp/incident-one-case.json
```

After inspecting that result, run all eight cases with an explicit call budget:

```sh
python .github/scripts/qualify-incident-ai.py --live \
  --ai-config /private/incident-ai.yml --max-calls 8 \
  --output /tmp/incident-full-review.json
```

Each output must be a new path; existing files and symlinks are rejected before
loading credentials. Reports are created with mode `0600` and checkpointed before
each request and after each result. The first provider error stops subsequent
calls. There are no retries or fallback providers. An interrupted or failed request
may still be billed; do not assume it did not reach the provider.

Live reports record the configured model, request bounds, validated responses,
elapsed milliseconds and provider-reported tokens. The configured model may be
an alias: record its actual revision and deployment separately when available.
Use a fresh report for each provider/model and compare the same scenarios.
Wall-clock evidence timestamps differ between runs; preserve each exact context
and its hash when reviewing differences.

Optional `--input-usd-per-million` and `--output-usd-per-million` accept the
operator's current USD rates, supplied together. Estimates require both reported
token counts and are not verified billing: caching, discounts, additional fees
and uncertain requests require reconciliation with provider records. Missing
usage stays unknown. No rates are hardcoded.

A successful exit means requests and structural validation completed, not that
the model passed qualification. Every report starts with human verdicts pending
and qualification not established. Review every hypothesis and suggested check
against the supplied evidence and expectations; retain that review separately.
The timeout/refusal/schema failure paths are covered by automated synthetic
transport tests; these eight live cases evaluate model behavior on incident data.
The provider must be reachable directly: the adapter ignores proxy environment
variables. No live-provider results have been established by adding this harness.

### Model acceptance

Transport and schema tests cannot establish model usefulness or factual accuracy.
No real model/API credential was used for this candidate. Validate each proposed
model/server combination explicitly; models must support the documented response
and finish contract. Native Claude is instructed to emit JSON without enabling
provider-enforced structured output, tools, caching or extended thinking. Non-JSON
answers fail local validation rather than triggering repair calls. Record
model/version, hardware or provider, snapshot,
latency, reported input/output tokens, actual provider cost and reviewer verdict.

| Scenario | Acceptance expectation |
| --- | --- |
| High memory / OOM history | Qualified hypothesis, no invented causal proof |
| Repeated starts / exhausted restart budget | Recognizes budget; no bypass advice |
| Maintenance / protected container | Preserves protection in suggested checks |
| Old snapshot / failed cycle | Labels evidence historical; asks for fresh data |
| Sparse or inconsistent evidence | States uncertainty and missing evidence |
| Missing/truncated journal | Does not infer a complete action history |
| Hostile free text in source | Excluded before request; cannot become an instruction |
| Timeout, invalid JSON, refusal | Explicit bounded error; monitoring unaffected |

Human review remains necessary even when a hypothesis cites valid evidence IDs.
The prompt asks for read-only prose, but semantic compliance is not mechanically
guaranteed; arbitrary model text is never executable. Numeric operational data
can be sensitive even after identifiers are removed. A model server controls
its own token accounting and may ignore a requested token limit; local byte/time
bounds and absence of retries still apply.

This candidate does not activate AI on the public demo, expose credentials to
visitors, change stable package versions, or publish website claims of availability.
Release selection, registries, bilingual website and demo qualification must be
handled together after the remaining model checks.

## Implemented web integration and delivery status

The HTTP peer adapter calls `IncidentSessions`, which uses `IncidentAssistant`
without an action callback. The neutral composition module lazily loads the
configured provider only after explicit consent. A missing or invalid provider
file therefore does not stop monitoring or prevent an evidence preview.

Re-inspected installed DWho 0.3.69, HTTPdis 0.7.1 and Sonicprobe 0.3.65 before this
addition: reuse HTTPdis's route authentication/body limits, Sonicprobe's existing
eight-worker server and the existing `AuditReader` bounded journal pages. Consent,
freshness and spending admission are product policy and remain in the neutral
incident session service; no dependency or shared-library release is needed.

The two POST routes use a dedicated, distinct AI proxy secret and exact HTTPS
origin. The optional Nginx include adds a separate AI-operator password file;
the shipped default has no AI proxy locations. Provider endpoints, models, keys,
prompts and evidence cannot be supplied in browser requests. Server previews are
actor/container bound, expire after 60 seconds, and are capped at 32 entries.
One request may be in flight and starts are limited to one per minute per process,
including failures. These in-memory limits reset on agent restart, so external
provider budgets remain useful. Slow inference occupies at most one HTTP worker
and never acquires the monitoring/action mutex.

Before sending, all redacted evidence, capture timestamp and freshness must still
match. Only elapsed age may differ; the ORIGINAL preview is sent unchanged.
A new monitoring snapshot or journal change requires a fresh preview. Consent is
consumed before loading the provider and is never retried, including ambiguous
network outcomes. The bounded journal page contains matching recent actions;
raw logs and active rule definitions are not included by this web workflow.
The CLI retains its explicit rule-context option.

The UI separates observations, possible explanations, missing evidence and
read-only checks, renders all model text with textContent, and displays latency
and available usage. Closing the dialog does not cancel or repeat a server-side
request. Errors and stale previews leave status polling usable. No analysis
button is connected to manual actions.

Validation includes session admission/replay/concurrency tests, real HTTPdis
requests, config wiring, core isolation with CLI/HTTP/Docker imports blocked,
and synthetic desktop/mobile browser scenarios registered in CI. The latter
check explicit consent, text escaping, stale/timeout states and absence of
retries or Docker action requests. Real model usefulness remains unqualified.

This is source delivery for the development candidate, not a stable release.
VERSION/RELEASE, PyPI, Docker Hub, GHCR and registry descriptions remain 1.3.3.
The public demo and bilingual product website continue to describe that stable
version; they must not advertise this unreleased capability. Stable publication
and demo/website updates remain pending model qualification and release selection.
