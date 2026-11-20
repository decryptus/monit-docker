# Copyright 2019-2026 Adrien Delle Cave
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit incident explanation interface; never initialize the monitoring engine."""
import json
import sys

from dwho.cli import write_json
from monit_docker.incident import IncidentAssistant, ExplanationError, DEFAULT_MAX_AGE, MAX_AGE, MAX_RULES
from monit_docker.incident import DEFAULT_LANGUAGE, EXPLANATION_LANGUAGES
from monit_docker.domain.identifiers import CONTAINER_ID_RE

OUTPUT_FORMATS = ('text', 'json')
MAX_RULE_BYTES = 2048


def render_report(report, stream):
    """Quote untrusted text so terminal escapes and newlines stay inert."""
    def quoted(value):
        return json.dumps(value, ensure_ascii=True, allow_nan=False)
    context = report['context']
    stream.write('Incident explanation: %s (%s)\n' % (report['status'], context['freshness']))
    stream.write('Explanation language: %s\n' % report.get('language', DEFAULT_LANGUAGE))
    stream.write('Container: %s\nObserved facts (model input):\n' % report['container_id'])
    for item in context['evidence']:
        stream.write('  %s %s\n' % (item['id'], quoted({key: value for key, value in item.items() if key != 'id'})))
    stream.write('Missing or limited evidence:\n')
    for item in context['missing_evidence']:
        stream.write('  %s\n' % quoted(item))
    if report['analysis'] is not None:
        stream.write('Unverified model hypotheses and suggested checks (human review required):\n')
        for key, entries in report['analysis'].items():
            stream.write('  %s: %s\n' % (key, quoted(entries)))
        stream.write('Elapsed milliseconds: %s; reported usage: %s\n' % (report['elapsed_ms'], quoted(report['usage'])))
    else:
        stream.write('Preview only. No model request was made.\n')


class MonitDockerSubCmdExplain:
    CMD_NAME = 'explain'

    def __init__(self, options):
        self.options = options

    @classmethod
    def load_subcmd_parser(cls, subparsers):
        parser = subparsers.add_parser(cls.CMD_NAME, help='explain one container incident without running actions')
        source = parser.add_mutually_exclusive_group()
        source.add_argument('--status-file', help='saved /v1/status JSON; never rendered as a template')
        source.add_argument('--status-url', help='loopback /v1/status URL (default: http://127.0.0.1:9808/v1/status)')
        parser.add_argument('--container-id', required=True, help='exact full Docker ID; sent to the model as an alias')
        parser.add_argument('--ai-config', help='separate plain YAML provider settings; required unless previewing')
        parser.add_argument('--preview', action='store_true', help='show the filtered context without contacting any model')
        parser.add_argument('--language', choices=tuple(EXPLANATION_LANGUAGES), default=DEFAULT_LANGUAGE,
                            help='language of model explanations: en or fr (default: en)')
        parser.add_argument('--max-age', type=int, default=DEFAULT_MAX_AGE, help='freshness limit in seconds (default: 90)')
        parser.add_argument('--rule', action='append', default=[], help='explicit rule to describe; never execute it or assume it is active')
        parser.add_argument('--output', choices=OUTPUT_FORMATS, default='text')
        return parser

    @classmethod
    def valid_subcmd_parser(cls, parser, options):
        if not CONTAINER_ID_RE.fullmatch(options.container_id):
            parser.error('--container-id requires an exact 64-character lowercase hexadecimal ID')
        if not options.preview and not options.ai_config:
            parser.error('--ai-config is required unless --preview is selected')
        if not 1 <= options.max_age <= MAX_AGE:
            parser.error('--max-age must be between 1 and 86400')
        if len(options.rule) > MAX_RULES or any(len(value.encode()) > MAX_RULE_BYTES for value in options.rule):
            parser.error('At most 10 rules of 2048 bytes each are accepted')
        # Existing global selectors are not a second selection language for this command.
        if any(getattr(options, key) for key in ('id', 'name', 'image', 'label', 'status', 'ctn_grp')):
            parser.error('explain selects only --container-id; global selectors are not accepted')

    def __call__(self):
        from monit_docker.adapters.incident_ai import load_provider, load_status, DEFAULT_STATUS_URL
        from monit_docker.adapters.rules import RuleParser
        from monit_docker.domain.errors import MonitoringError
        options = self.options
        try:
            # Preview does not even read the provider settings or credential file.
            provider = None if options.preview else load_provider(options.ai_config)
            status = load_status(options.status_file, options.status_url or DEFAULT_STATUS_URL)
            try:
                rules = tuple(RuleParser().parse(value) for value in options.rule)
            except (MonitoringError, ValueError, TypeError):
                raise ExplanationError('invalid_rule') from None
            records, more = None, False
            if options.audit_file:
                from monit_docker.audit_query import AuditReader, AuditQuery, QueryError
                from monit_docker.audit import AuditJournal
                try:
                    page = AuditReader(AuditJournal(options.audit_file, options.audit_max_bytes, options.audit_files)).page(
                        AuditQuery({'container': options.container_id, 'category': 'action'}), 'local-incident-explanation')
                except QueryError:
                    raise ExplanationError('audit_unavailable') from None
                records, more = page['records'], bool(page['next_cursor'])
            report = IncidentAssistant(provider).explain(status, options.container_id, preview=options.preview,
                language=options.language, max_age=options.max_age, records=records, history_more=more, rules=rules)
        except ExplanationError as error:
            if options.output == 'json':
                write_json(dict(status='error', reason=error.reason), ensure_ascii=True, sort_keys=True)
            else:
                print('Incident explanation failed: ' + error.reason, file=sys.stderr)
            return error.code
        if options.output == 'json':
            write_json(report, ensure_ascii=True, sort_keys=True)
        else:
            render_report(report, sys.stdout)
        return 0
