"""Small, explicit manual-command vocabulary shared by service and engine."""

ALLOWED_STATES = {
    'start': ('created', 'exited'),
    'stop': ('running', 'restarting'),
    'restart': ('running',),
}
