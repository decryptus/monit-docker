# Copyright 2019-2026 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Command-line adapters for audit export, forwarding and migration."""

import sys

from dwho.cli import write_json
from monit_docker.composition import audit_journal


class MonitDockerSubCmdAudit:
    CMD_NAME = 'audit-export'
    CMD_HELP = 'export retained actions and notifications without connecting to Docker'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        parser.add_argument('--format', choices=('jsonl', 'csv'), default='jsonl')
        parser.add_argument('--category', choices=('action', 'notification'))
        parser.add_argument('--since', help='UTC/RFC3339 timestamp; export events at or after this time')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        from datetime import datetime
        if not options.audit_file:
            parser.error('--audit-file is required for audit commands')
        if options.since:
            try:
                options.since = datetime.fromisoformat(options.since.replace('Z', '+00:00'))
                if options.since.tzinfo is None:
                    raise ValueError('Timezone required')
            except ValueError:
                parser.error('--since must be an ISO timestamp with timezone')

    def filtered_records(self, records):
        from datetime import datetime
        return (record for record in records
                if (not self.options.category or record.get('category') == self.options.category)
                and (not self.options.since or datetime.fromisoformat(record['timestamp'].replace('Z', '+00:00')) >= self.options.since))

    def records(self):
        # HTTPS forwarding retains its existing materialized batch contract.
        return list(self.filtered_records(audit_journal(self.options, explicit=True).read()))

    def __call__(self):
        from monit_docker.audit import export_events
        with audit_journal(self.options, explicit=True).iter_records() as records:
            export_events(self.filtered_records(records), sys.stdout, self.options.format)
        return 0


class MonitDockerSubCmdAuditSend(MonitDockerSubCmdAudit):
    CMD_NAME = 'audit-send'
    CMD_HELP = 'forward retained audit events to an HTTPS service; keep the local journal'

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = super().load_subcmd_parser(subparsers)
        parser.add_argument('--url', required=True, help='HTTPS JSON event receiver')
        parser.add_argument('--token-file', help='optional Bearer secret file')

    def __call__(self):
        from monit_docker.audit_forward import send_events
        count = send_events(self.records(), self.options.url, self.options.token_file)
        print('%d audit events acknowledged; local journal retained' % count, file=sys.stderr)
        return 0


class MonitDockerSubCmdAuditMigrate:
    CMD_NAME = 'audit-migrate'
    CMD_HELP = 'validate or copy retained journals to schema 2 with an original-byte backup'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help=cls.CMD_HELP)
        mode = parser.add_mutually_exclusive_group()
        mode.add_argument('--dry-run', action='store_true', help='validate and report without writing output (default)')
        mode.add_argument('--apply', action='store_true', help='create a verified migration bundle; never replace source files')
        parser.add_argument('--output-dir', help='new directory for backup, converted journals and manifest')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not options.audit_file:
            parser.error('--audit-file is required for audit commands')
        if options.apply and not options.output_dir:
            parser.error('--apply requires --output-dir')
        if options.output_dir is not None and not options.output_dir.strip():
            parser.error('--output-dir must not be empty')

    def __call__(self):
        from monit_docker.audit import AuditError
        from monit_docker.audit_migrate import migrate_journal
        try:
            report = migrate_journal(audit_journal(self.options, explicit=True),
                                     self.options.output_dir, self.options.apply)
        except AuditError as error:
            print('audit-migrate: %s' % error, file=sys.stderr)
            return error.code
        write_json(report, ensure_ascii=True, sort_keys=True)
        return 0
