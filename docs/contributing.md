# Contributor documentation

This guide is for people changing or maintaining monit-docker. For installation, configuration and everyday use, start with the [user documentation](https://github.com/decryptus/monit-docker/blob/master/README.md).

## Development

The codebase is being separated into a transport-neutral monitoring core and
thin delivery interfaces. See [Architecture](https://github.com/decryptus/monit-docker/blob/master/docs/architecture.md) for the
dependency rules, compatibility guarantees, and component
boundary.

Install the dependencies and run the regression tests with Python 3:

```sh
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

Build the documentation and check for broken internal references:

```sh
python -m pip install -r docs/requirements.txt
python -m sphinx -n -W --keep-going -b html docs docs/_build/html
```

Build the checked-out source with `docker build -t monit-docker:local .`. The Dockerfile installs this checkout in a virtual environment instead of fetching the published `monit-docker` package.

## Documentation rules

Keep user instructions and contributor material separate. The repository [engineering requirements](https://github.com/decryptus/monit-docker/blob/master/AGENTS.md) define the review and validation rules. Preserve user-facing compatibility, security and recovery guidance when moving internal explanations.
