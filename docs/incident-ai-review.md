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

## Boundaries verified by automated tests

- Exact ID matching; other containers, raw text, names, IDs and secret canaries
  excluded from outgoing context. Preview never loads credentials or calls a model.
- Fresh versus retained/old/future snapshots; unavailable metrics remain missing;
  maintenance, protection and restart limits remain explicit evidence.
- Bounded journal history, closed action enums and normalized supplied rules;
  custom command text is excluded and rule activation is never asserted.
- Real loopback HTTP exchanges with synthetic Ollama/OpenAI-compatible servers;
  one request, protocol fields, header-only key, usage allowlist, incomplete output,
  refusal, tool calls, malformed JSON and unknown evidence IDs.
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
model/server combination explicitly; models must support the documented JSON
mode and finish contract. Record model/version, hardware or provider, snapshot,
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

## Planned web integration contract

This is a design for subsequent implementation; this candidate still has no AI
HTTP route or web interface. Reuse the neutral `IncidentAssistant` service through
an HTTP peer adapter after model qualification. Keep provider configuration and
credentials on the server; browser requests must not supply arbitrary endpoints,
models, keys or prompts. Preserve operator choice through server configuration.

The interface should first display the redacted evidence and freshness for one
exact container ID, then allow an explicit explanation request. The server must
reject changed evidence between preview and submission instead of silently
sending additional data. Integrate with existing HTTPdis authentication and
server-side authorization; a public demo must not acquire paid inference access.
Define and test AI permission, concurrency and request-rate limits before adding
the route: read access alone must not implicitly authorize spending.

Show observations, uncertain hypotheses, missing evidence and read-only checks
separately, with evidence references, latency and available usage. Render model
text as escaped text. Explicit loading, stale-evidence, unavailable-provider,
refusal and timeout states must leave monitoring usable. No suggestion can invoke
an action handler, command, restart or protection change. Reuse HTTPdis routing,
authentication and request-size enforcement; do not duplicate the monitoring
engine or provider logic in the browser.
