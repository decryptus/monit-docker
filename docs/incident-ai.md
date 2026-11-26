# Incident assistance

Available since **2.0.0**, this optional open-source feature adds `monit-docker explain`: an explicit explanation of one container
from cached observations, with Ollama, an OpenAI-compatible endpoint or Claude's
native Anthropic Messages API.
It requires no broker account or additional product licence. Remote API usage
is billed by your provider. No model is contacted by normal monitoring.

The feature includes the CLI and an opt-in HTTP/web preview-and-explain workflow.
Model quality and real-provider compatibility still need field qualification;
automated tests use synthetic responses, not a production model.

The endpoint, model and private key-file path are configurable. Providers must
support the implemented OpenAI-compatible Chat Completions, Ollama or Anthropic contract;
other protocols need an adapter. Compatibility is checked per model. Contributors
can compare providers with the [synthetic evaluation harness](incident-ai-review.md#reproducible-synthetic-evaluation).

## Preview the evidence first

Install a 2.0.0 or later source checkout (current release: 2.0.2) in a virtual environment:

```sh
python3 -m venv .venv
.venv/bin/pip install .
```

Use the **full 64-character lowercase container ID** from your existing status
view. The default source is the running agent's loopback
`http://127.0.0.1:9808/v1/status`; the command never connects to Docker itself.

```sh
.venv/bin/monit-docker explain --container-id "$CONTAINER_ID" --preview
```

Alternatively, read a saved `/v1/status` response with `--status-file status.json`.
The bundled synthetic example can be previewed without an agent or model:

```sh
.venv/bin/monit-docker explain \
  --status-file examples/ai/status.json \
  --container-id aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa \
  --preview --output json
```

Its fixed timestamp is intentionally historical. Freshness uses the collection
timestamp and reported age, with a default maximum of 90 seconds. A failed
collection's retained snapshot is always historical. Use `--max-age SECONDS`
(1–86400) to change the limit; this does not refresh the measurements.

## Choose a provider explicitly

Copy one of the settings files in `examples/ai/` to a local configuration. Replace
`MODEL_ID` with a model available on that endpoint. Settings are plain YAML,
separate from monitoring configuration, without template rendering or imports.

Local Ollama example:

```yaml
provider: ollama
endpoint: http://127.0.0.1:11434/api/chat
model: MODEL_ID
timeout: 30
max_output_tokens: 1024
```

An OpenAI-compatible endpoint must accept non-streaming Chat Completions,
`response_format: {type: json_object}` and `max_completion_tokens`. Compatibility
with these fields depends on the chosen model/server. Set `allow_remote: true`
to use a remote HTTPS endpoint. Plain HTTP is accepted only for literal loopback
addresses; use `127.0.0.1` or `::1`, not `localhost`.

```yaml
provider: openai-compatible
endpoint: https://api.openai.com/v1/chat/completions
model: MODEL_ID
allow_remote: true
api_key_file: /run/secrets/monit-docker-ai-key
timeout: 30
max_output_tokens: 1024
```

For Claude directly through Anthropic, use `examples/ai/anthropic.yml`:

```yaml
provider: anthropic
endpoint: https://api.anthropic.com/v1/messages
model: MODEL_ID
allow_remote: true
api_key_file: /run/secrets/monit-docker-anthropic-key
timeout: 30
max_output_tokens: 1024
```

This adapter uses the native Messages API with API version `2026-10-21`, bearer
authentication and a required dedicated API key scoped to one workspace.
Multi-workspace keys, cloud-provider authentication and OAuth are not supported.
Select a Claude model available to your account. The adapter sends a system
instruction requesting JSON and validates the result locally; it does not request
provider-enforced structured output. Markdown-wrapped JSON, refusals, truncated
answers, thinking/tool blocks and non-text content are rejected, without retry.
Only one text block ending with `end_turn` is accepted. Real-model qualification
is still required. No tools, prompt caching or extended thinking are requested.
If the provider nevertheless reports cached input, input usage is withheld rather
than presented as a complete count for the qualification cost estimate.

Create the key file privately, owned by the account running the command, with
mode `0600`. Store only the key in it. Do not put the key on the command line or
in YAML. A relative key path resolves beside the AI configuration file. Secret
files must be regular files, not symlinks. Never commit keys or private settings.

```sh
.venv/bin/monit-docker explain --container-id "$CONTAINER_ID" \
  --ai-config /path/to/ai.local.yml
```

Each invocation makes at most one model request, without retries. `--preview`
does not even read provider settings or the key file. Proxy environment variables
and redirects are ignored. The model's reported token counts and request latency
are returned; no currency estimate is invented. Review the provider's prices.

## What the model receives

The outgoing context contains an alias (`container-1`), allowlisted numeric
measurements, status, health, maintenance/protection state and restart budget.
Names, real IDs, labels, environment variables, paths, raw logs and command
contents are excluded. Other containers are excluded. These selected metrics
can still reveal operational information: review the preview before remote use.
The local report contains the real container ID for your own correlation.

To include recent action evidence, explicitly pass the existing local journal:

```sh
.venv/bin/monit-docker --audit-file /path/to/audit.jsonl explain \
  --container-id "$CONTAINER_ID" --ai-config /path/to/ai.local.yml \
  --rule 'mem_percent > 90 ? restart'
```

Only up to 20 matching action records from one bounded journal page are included.
Free-text journal fields are excluded; truncated history is labelled partial.
`--rule` accepts up to 10 explicit numeric/status/health rules without configured
aliases. The command describes their conditions and action kinds; it does not
read active configuration, prove that a supplied rule is active, or execute it.
Custom command contents remain opaque.

## Read the result and its limits

Observed facts are assembled locally. Model hypotheses and suggested checks are
separate and unverified. References must point to supplied evidence IDs, but a
valid reference does **not** prove that the model's interpretation is correct.
Missing or stale data remain visible. Suggestions are text for human review;
there is no execution path, restart, configuration change or protection bypass.

`--output json` returns a structured report. Operational failures use exit code
120 and a stable `reason`, without provider bodies or credentials. Invalid CLI
arguments use the existing argparse exit code 2. Monitoring continues independently.

| Boundary | Limit |
| --- | --- |
| Saved status / HTTP status response | 2 MiB |
| Outgoing evidence | 24,000 bytes |
| Provider response / model JSON content | 65,536 / 16,000 bytes |
| Provider headers / JSON nesting | 16,384 bytes / 64 levels |
| Request deadline | 30 seconds by default; configurable 1–120 |
| Requested output tokens | 1,024 by default; configurable 128–2,048 |
| Each output list / each text | 8 items / 1,000 characters |

Token limits are requested from the provider; byte and time limits are enforced
locally. Refusals, tool calls, incomplete responses, unknown evidence IDs and
invalid JSON are rejected. libcurl must support asynchronous DNS for the total
deadline. Slow local models may time out; there is no automatic fallback or retry.

[Guide en français](incident-ai-fr.md) · [Contributor review](incident-ai-review.md)

## Optional web workflow

Use matching **agent and UI images, 2.0.0 or later** (current release: 2.0.2). AI remains disabled unless all three server
options are configured (CLI flags or the corresponding `serve:` YAML keys):

```yaml
serve:
  ai_config: /private/incident-ai.yml
  ai_token_file: /private/ai-proxy-token
  ai_origin: https://monitor.example
  trust_proxy_user: true
```

`ai_config` is the same provider YAML used by the CLI. Keep it and its private
key file on the agent. `ai_token_file` contains a new 64-character hex proxy
secret, distinct from status, metrics, journal, notification and action secrets.
Generate it locally, for example with `openssl rand -hex 32`, under `umask 077`.
Do not put either secret into browser JavaScript or a URL. The AI permission does
not require enabling manual Docker actions or granting journal HTTP access.

For the optional Nginx UI, mount the repository's `examples/ai/nginx.conf` at
`/etc/nginx/monit-ai.conf` (read-only). Also mount:

- `/run/secrets/ai-htpasswd`: a separate HTTP Basic password file containing only
  authorized AI operators. Operators also need normal dashboard access; using
  the same username/password in both files avoids repeated browser prompts.
- `/run/secrets/proxy-ai.conf`: one directive containing the same dedicated AI
  proxy secret: `proxy_set_header X-Monit-AI-Token "<64-character-hex-secret>";`.

Use the exact external HTTPS origin in `ai_origin`. The proxy overwrites the
caller identity and AI secret, rejects non-POST requests and never retries an
inference upstream. Its 130-second read timeout covers the provider's maximum
120-second timeout. The default shipped UI has no AI proxy locations. Keep the
agent port private. For container deployments, explicitly provide the agent a
route to the chosen provider: the example `agent` network is internal and has no
Internet egress; loopback Ollama refers to the agent's own network namespace.
No public demo inference access is enabled by these source changes.

Click **Explain incident** for a container. Review the redacted observations,
then click **Request explanation**. A preview makes no model call and does not
load the provider key. The web context includes a bounded page of recent matching
action events when the agent journal is available, with incomplete history
labelled explicitly. It excludes raw logs and active rule definitions. Journal
read failure blocks the request instead of silently sending a different context.

The server retains up to 32 previews for 60 seconds, bound to the authenticated
operator and exact container ID. A new snapshot, changed journal or changed
freshness requires **Refresh preview**. The exact preview is sent, with its age
at preview time. One AI request may run at a time, with at least 60 seconds
between starts across operators, including failed calls. These limits are per
agent process and reset on restart; they are not a provider billing quota.

Observations, possible explanations, missing evidence and read-only checks are
shown separately. Model text is escaped; none of it can invoke a Docker action.
Latency and reported input/output tokens are shown; absent usage remains unknown.
Closing the dialog does not cancel a request already admitted by the server.
An error or lost response may still be billed, and no retry happens automatically.
Monitoring and manual-action scheduling continue independently.

### HTTP contract

Both endpoints require JSON, the dedicated `X-Monit-AI-Token`, matching `Origin`,
and (with `trust_proxy_user`) the proxy-overwritten `X-Monit-Actor`. Bodies are
limited to 1 KiB and unknown fields are rejected. Browser-supplied model names,
endpoints, prompts, keys or evidence are never accepted.

| Endpoint | Exact request fields | Result |
| --- | --- | --- |
| `POST /v1/ai/preview` | `container_id` | Preview report, opaque `preview_id`, `expires_in` |
| `POST /v1/ai/explain` | `container_id`, `preview_id` | Completed report with context, analysis, latency and usage |

`/v1/status` adds `ai_enabled` for capability discovery; it is not authorization.
Disabled AI routes return 404. Authentication/origin rejection returns 403;
invalid request fields return 400; expired, consumed or changed previews return
409; busy/rate/capacity limits return 429; provider timeout returns 504; other
provider or journal failures return 503 with a stable `error` reason. Every
attempt that reaches provider loading consumes its preview, including errors.

## Explanation language

Use `--language fr` or `--language en` with `monit-docker explain` (default: `en`).
The web sign-in/account selector and AI dialog share a persisted preference,
initially selected from supported browser languages with English fallback.
Static authentication and AI text uses self-hosted i18next and separate catalogs.
Other dashboard views keep their existing language; this is not a full UI translation.

The preview and explain JSON bodies accept an optional `language` (`en` or `fr`).
An omitted preview language defaults to English. An omitted explain language uses
the stored preview language. Unsupported values return `invalid_language` (400).
A different language at submission returns `language_changed` (409), consumes
that preview and makes no provider call. The UI creates a new preview when its
language changes; inference still requires explicit consent.

Only explanatory text is translated. JSON keys, evidence IDs, container identifiers,
raw values and API error codes stay unchanged. A language instruction cannot
ensure a particular model's quality; real-model FR/EN review is deferred.
Run the qualification tool with `--language fr` or `--language en` to record the
actual prompt hash and language alongside results.
