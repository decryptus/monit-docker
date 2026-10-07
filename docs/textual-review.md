# Textual validation and media

The Textual adapter uses `dwho.tui.textual` and existing product services.
Base commands remain independent of optional Textual imports. Run the collection
check and the base and `textual_tests` unittest roots separately.

```sh
python .github/scripts/check-test-collection.py --runner unittest tests textual_tests
python -m unittest discover -s tests -v
python -m unittest discover -s textual_tests -v
python scripts/capture_textual.py --output /tmp/textual-captures --png
```

PNG rendering requires `resvg-py`; do not substitute CairoSVG because its terminal
font layout differs. Captures use the actual interface with labelled synthetic
fixtures and include source/shared-library hashes. Inspect normal, unavailable,
confirmation, progress and result states. Do not claim synthetic captures prove
production authorization or deployment. Capture scripts and website generators
must use the released source revision.

Auton retains explicit execution preparation, guided arguments, scenario/group
selection, result export and read-only reconciliation. Execution services retain
server ACLs, ordered failover and uncertain-outcome handling. monit-docker retains
the existing observation-only contract and does not execute interventions.
