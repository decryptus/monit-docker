"""Send a JSON notification using DWho's registry, YAML config and Mako template."""
import argparse
import json
import os
from pathlib import Path
import sys

from dwho.classes.notifiers import DWhoPushNotifications


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config-dir', required=True, type=Path, help='Directory containing http.yml and http.json')
    parser.add_argument('--token-file', required=True, type=Path, help='Bearer credential file')
    parser.add_argument('--payload', default='-', help='JSON file, or - for stdin')
    args = parser.parse_args()
    try:
        if args.payload == '-':
            payload = json.load(sys.stdin)
        else:
            payload = json.loads(Path(args.payload).read_text())
        if not isinstance(payload, dict):
            raise ValueError('Expected a JSON object')
        token = args.token_file.read_text().strip()
        if not token or any(c.isspace() for c in token):
            raise ValueError('Invalid Bearer token')
        # DWho resolves template filenames relative to the working directory.
        os.chdir(args.config_dir.resolve())
        dispatcher = DWhoPushNotifications(config_path='.')
        if not dispatcher.notifications.get('http', {}).get('tpl'):
            raise ValueError('Missing HTTP template')
        # DWhoNotifiers selects its HTTP/HTTPS handler from general.uri.
        # Use strict send(): the legacy callable can log and suppress failures.
        dispatcher.send({'notification': payload, 'http_token': token}, names=['http'])
    except Exception as error:
        # Error text/URLs may contain credentials. Keep diagnostics generic.
        print('HTTP notification failed (%s)' % type(error).__name__, file=sys.stderr)
        return 1
    print('HTTP notification accepted')
    return 0


if __name__ == '__main__':
    sys.exit(main())
