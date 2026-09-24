"""Small, explicit manual-command vocabulary shared by service and engine."""

ALLOWED_STATES = {
    'start': ('created', 'exited'),
    'stop': ('running', 'restarting'),
    'restart': ('running',),
    'restart-reset': ('created', 'running', 'paused', 'restarting', 'exited', 'dead'),
}
