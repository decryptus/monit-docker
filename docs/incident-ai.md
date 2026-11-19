# Incident assistance (development candidate)

This optional, open-source feature is **not included in release 1.3.3**. The
candidate adds `monit-docker explain`: an explicit explanation of one container
from cached observations, with an Ollama model or an OpenAI-compatible endpoint.
It requires no broker account or additional product licence. Remote API usage
is billed by your provider. No model is contacted by normal monitoring.

The initial interface is the CLI. There is no AI button or AI HTTP route yet.
Model quality and real-provider compatibility still need field qualification;
automated tests use synthetic responses, not a production model.

## Preview the evidence first

Install the candidate from its source checkout in a virtual environment:

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
