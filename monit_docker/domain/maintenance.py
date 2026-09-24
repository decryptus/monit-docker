"""Finite, per-container suspension of automatic rule actions."""

MAX_MAINTENANCE_SECONDS = 86400
MAINTENANCE_COMMANDS = {'maintenance-15m': 900, 'maintenance-1h': 3600, 'maintenance-off': 0}
MAINTENANCE_STATES = ('created', 'running', 'paused', 'restarting', 'exited', 'dead')
