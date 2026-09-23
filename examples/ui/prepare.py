#!/usr/bin/env python3
"""Prepare optional UI secrets without printing passwords or action tokens.

Requires openssl. Existing files are never silently overwritten.
"""

import argparse
import getpass
import os
from pathlib import Path
import re
import secrets
import subprocess


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--actions', action='store_true', help='prepare the optional write proxy secret')
    parser.add_argument('--self-signed', action='store_true', help='create a localhost-only development certificate')
    args = parser.parse_args()
    target = Path(__file__).resolve().parent / 'secrets.local'
    if target.exists():
        parser.error('secrets.local already exists; see the guide for rotation')
    username = input('Dashboard username: ').strip()
    if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}', username):
        parser.error('use 1–64 letters, digits, dots, underscores or hyphens for the username')
    password = getpass.getpass('Dashboard password (12+ characters): ')
    if len(password) < 12 or '\n' in password or '\r' in password:
        parser.error('password must contain at least 12 characters and no newlines')
    if getpass.getpass('Repeat password: ') != password:
        parser.error('passwords do not match')
    # SHA-512 crypt, supported by the Linux Nginx image. Password is on stdin,
    # never in process arguments, Compose variables or generated instructions.
    hashed = subprocess.run(['openssl', 'passwd', '-6', '-stdin'], input=password + '\n',
                            text=True, stdout=subprocess.PIPE, check=True).stdout.strip()
    os.umask(0o077)
    target.mkdir(mode=0o700)
    (target / 'htpasswd').write_text(username + ':' + hashed + '\n')
    # Nginx workers need the hash; directory on the host remains private.
    (target / 'htpasswd').chmod(0o644)
    token = secrets.token_hex(32) if args.actions else ''
    (target / 'proxy-action.conf').write_text('proxy_set_header X-Monit-Action-Token "' + token + '";\n')
    if args.actions:
        (target / 'action-token').write_text(token + '\n')
    if args.self_signed:
        subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                        '-days', '7', '-subj', '/CN=localhost',
                        '-addext', 'subjectAltName=DNS:localhost,IP:127.0.0.1',
                        '-keyout', str(target / 'tls.key'), '-out', str(target / 'tls.crt')],
                       check=True)
    print('Prepared secrets.local. ' + ('Development certificate expires in 7 days.' if args.self_signed
                                      else 'Add your TLS certificate as tls.crt and private key as tls.key.'))


if __name__ == '__main__':
    main()
