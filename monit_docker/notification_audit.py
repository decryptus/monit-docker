"""Normalize authenticated Alertmanager notifications without storing secret payloads."""
from monit_docker.audit import fingerprint


class NotificationAudit:
    def __init__(self, journal, token):
        self.journal, self.token = journal, token

    def receive(self, payload):
        if (not isinstance(payload, dict) or payload.get('version') != '4'
                or payload.get('status') not in ('firing', 'resolved')
                or not isinstance(payload.get('receiver'), str)
                or len(payload['receiver']) > 256
                or not isinstance(payload.get('groupKey'), str)
                or len(payload['groupKey']) > 2048
                or payload.get('truncatedAlerts', 0) != 0):
            raise ValueError('Invalid Alertmanager notification')
        alerts = payload.get('alerts')
        if not isinstance(alerts, list) or not 1 <= len(alerts) <= 100:
            raise ValueError('Expected 1..100 alerts')
        events = []
        for alert in alerts:
            if not isinstance(alert, dict) or alert.get('status') not in ('firing', 'resolved'):
                raise ValueError('Invalid alert')
            labels = alert.get('labels')
            if not isinstance(labels, dict) or not isinstance(labels.get('alertname'), str) or not labels['alertname'] or len(labels['alertname']) > 256:
                raise ValueError('Invalid alert name')
            for field in ('fingerprint', 'startsAt', 'endsAt'):
                if not isinstance(alert.get(field, ''), str) or len(alert.get(field, '')) > 256:
                    raise ValueError('Invalid alert metadata')
            events.append(dict(source='automatic', actor='alertmanager', channel=payload['receiver'],
                               notification_id=fingerprint((payload['groupKey'], alert.get('fingerprint'), alert.get('startsAt'))),
                               alert_name=labels['alertname'], alert_status=alert['status'],
                               result='received', delivery_status='not_reported'))
        # Validate the entire batch before writing. A retry can repeat receipts;
        # notification_id correlates duplicates and firing/resolved transitions.
        for fields in events:
            self.journal.record('notification', 'received', **fields)
        return len(events)
