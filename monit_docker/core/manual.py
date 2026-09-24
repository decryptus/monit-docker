"""Small, explicit manual-command vocabulary shared by service and engine."""

from monit_docker.domain.maintenance import MAINTENANCE_COMMANDS, MAINTENANCE_STATES

ALLOWED_STATES = {
    'start': ('created', 'exited'),
    'stop': ('running', 'restarting'),
    'restart': ('running',),
    'restart-reset': ('created', 'running', 'paused', 'restarting', 'exited', 'dead'),
}

ALLOWED_STATES.update(dict.fromkeys(MAINTENANCE_COMMANDS, MAINTENANCE_STATES))
