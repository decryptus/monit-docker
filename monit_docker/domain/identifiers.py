"""Shared exact identifier contracts; selectors have their own grammar."""
import re

HEX_64_RE = re.compile(r'[0-9a-f]{64}')
CONTAINER_ID_RE = HEX_64_RE
REQUEST_ID_RE = re.compile(r'[0-9a-f]{32}')
