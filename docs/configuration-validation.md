# Configuration validation

The existing CheckedConfiguration loader used by inspection and execution now
uses XYS for the root section schema, string-key mappings and string lists.
Existing section/entry whitelists, error locations, nonempty/whitespace checks,
identifier grammars and business validation remain in place. Mako rendering,
relative imports, aliases and comma literals are unchanged. Stats/TUI still omit
unused command and condition definitions when include_rules=False; this does not
skip validation of root section shapes.

XYS (Sonicprobe >= 0.3.57) validates parsed Python data; it does not replace the
YAML loader, resolve imports, initialize services or grant permissions. Schemas
are compiled once. These schemas use no modifiers and do not silently convert
values. The existing numeric normalization and semantic checks still apply.

Known application fields are validated explicitly. Extension settings remain
available where the existing contract permits them; this is not universal typo
detection for plugin configuration. Malformed section/component shapes now fail
with a configuration error instead of incidental attribute/update exceptions.
New validation errors do not include configuration values or credential contents.

`tests/test_configuration_schema.py` covers valid/invalid shapes and compatibility
at the loader boundary. Run the collection guard before the unittest suite:

```sh
python .github/scripts/check-test-collection.py --runner unittest tests
python -m unittest discover -s tests -v
```

These tests use synthetic data and mocked adapters or loopback services. They do
not establish provider availability or production acceptance.
