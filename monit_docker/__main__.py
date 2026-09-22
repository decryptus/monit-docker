"""Allow ``python -m monit_docker`` to behave like ``monit-docker``."""

from __future__ import absolute_import

import sys

from .cli import argv_parse_check, main


if __name__ == '__main__':
    sys.exit(main(argv_parse_check()))
