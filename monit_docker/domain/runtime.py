"""Optional runtime checks shared by collectors, rules and outputs."""

EVENT_RESOURCES = ('oom_events', 'starts_recent')
PID_RESOURCES = ('pids_current', 'pids_limit', 'pids_percent')
RUNTIME_RESOURCES = EVENT_RESOURCES + PID_RESOURCES
RUNTIME_METADATA = ('event_window_seconds', 'event_window_end', 'event_history_complete')
DEFAULT_EVENT_WINDOW = 300
MAX_EVENT_WINDOW = 86400


def valid_event_window(value):
    return type(value) is int and 1 <= value <= MAX_EVENT_WINDOW
