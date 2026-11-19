# Incident assistance: implementation and qualification

Development candidate, 19 November 2026. Not a release announcement.
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
