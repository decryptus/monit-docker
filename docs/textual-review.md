# Textual candidate: validation and media

## Architecture and rollout

The optional presentation imports the shared DWho dashboard. The product supplies
rows and handles navigation. Container and journal read views are covered. No rule evaluation or intervention is started by the terminal adapter.

The default interface remains curses. This branch depends on the separate DWho
candidate `fdc3a12b1547c9f78ebec28e1e53770444f34caf`; it must be reconciled with
the concurrent DWho/HTTPdis audit before merge. No release/version bump or
production website deployment is included.

## Local validation (Python 3.12)

The discovery roots are `tests`, `textual_tests` and `.github/tests`.
They contain 440 unittest cases: 407 base regressions,
4 optional Textual cases and 29 helper cases. Intentional skips:
18 opt-in Docker integration cases and one pytest-only helper case. Redis/Docker prerequisites remain separate CI gates.

Optional tests exercise the real Textual app with headless key/click input,
existing service boundaries and offline fixtures. Packages are built and the
optional suite is also run against the installed wheel outside the checkout.
This is not production Docker acceptance or a complete SSH-terminal matrix.

```sh
python .github/scripts/check-test-collection.py --runner unittest tests textual_tests .github/tests
python -m unittest discover -s tests -v
python -m unittest discover -s textual_tests -v
python -m unittest discover -s .github/tests -v
```

## Reproducible captures and demonstration

```sh
PYTHONPATH=. python scripts/capture_textual.py --output /tmp/monit-docker-textual-captures
```

This drives the actual interface and exports three SVG screens with synthetic
fixtures. The accompanying manifest records source hashes, renderer version,
Git revision when available and whether the checkout has modifications. No live
credentials or infrastructure are captured. The optional CI job publishes these
as review artifacts, without replacing release screenshots.

The demonstration modules run interactively from the user guide. Existing
curses captures and production website source pins are preserved. After release
acceptance, update the website's source pin and regenerate shared manual
screenshots from this capture script; review scenario legends and source hashes.
Auton's README must continue to use the shared `auton.run/manual-captures/`
assets, not committed copies. Keep the same principle for monit-docker.

For the video, replace the terminal passages only once the new interaction is
accepted. Existing narration and unrelated scenes can be retained where they
still match. These candidate captures do not imply that the complete published
video has been regenerated.

## Post-audit integration — 2026-10-07

This release candidate targets monit-docker 1.1.0 with the optional shared
DWho 0.3.65 foundation, including published DWho 0.3.64 audit fixes.
HTTPdis 0.6.34 and Sonicprobe 0.3.58 are the current published dependencies.
The historical validation counts above are retained; the final pull request
records the coordinated CI checks and generated captures. Publication and
replacement of shared website media await the agreed visual acceptance.
