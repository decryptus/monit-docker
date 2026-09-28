"""Explicit opt-in terminal presentation; importing this module does not load curses."""
import json
import os
import sys

POLL_MS = 250
MIN_HEIGHT = 7
MIN_WIDTH = 35
QUIT_KEYS = ('q', 'Q', '\x1b')
TAB_KEYS = ('\t', 'j', 'c')
DETAIL_KEYS = ('\n', '\r')


def safe_text(value):
    return ''.join(char if char.isprintable() else ' ' for char in str(value))


def percent(value):
    return 'unavailable' if value is None else '%s%%' % value


def screen_loop(screen, observation):
    import curses
    from dwho.tui import put, view_details
    screen.keypad(True)
    screen.timeout(POLL_MS)
    try:
        curses.curs_set(0)
    except curses.error:
        pass
    journal, selected = False, 0
    while True:
        if not sys.stdin.isatty():
            raise OSError('Terminal disconnected')
        data = observation.snapshot()
        height, width = screen.getmaxyx()
        screen.erase()
        rows = data['records'] if journal else data['containers']
        selected = min(selected, max(0, len(rows) - 1))
        put(screen, 0, 'monit-docker | READ ONLY | ' + ('Journal' if journal else 'Containers'))
        error = data['journal_error'] if journal else data['collection_error']
        if error is not None:
            status = 'Unavailable: %s' % error
        elif journal and not data['journal_enabled']:
            status = 'Journal disabled: specify --audit-file'
        elif data['updated'] is None:
            status = 'Collecting...'
        elif data['stale']:
            status = 'STALE: collection has not completed recently'
        else:
            status = 'Updated %.0fs ago | refresh %ss' % (data['age'], observation.interval)
        if journal and data['journal_more']:
            status += ' | Latest page only; more history exists'
        put(screen, 1, safe_text(status))
        put(screen, height - 1, 'Up/Down: select | Enter: details | Tab: view | q: quit')
        if height < MIN_HEIGHT or width < MIN_WIDTH:
            put(screen, 3, 'Terminal too small; resize or q to quit')
        else:
            count = height - 4
            offset = (selected // count) * count
            if not rows and error is None and data['updated'] is not None:
                put(screen, 3, 'No entries in this view')
            for index, row in enumerate(rows[offset:offset + count], offset):
                if journal:
                    label = '%s %s %s %s' % (row.get('timestamp', ''), row.get('container_name') or '',
                                             row.get('action') or row.get('category', ''), row.get('result', ''))
                else:
                    label = '%s | %s | health=%s | CPU=%s | memory=%s' % (
                        row.get('name'), row.get('status'), row.get('health'),
                        percent(row.get('cpu_percent')), percent(row.get('mem_percent')))
                put(screen, 3 + index - offset, safe_text(label),
                    curses.A_REVERSE if index == selected else 0)
        screen.refresh()
        try:
            key = screen.get_wch()
        except curses.error:
            continue
        if key in QUIT_KEYS:
            return 0
        if key in TAB_KEYS:
            journal = not journal if key == '\t' else key == 'j'
            selected = 0
        elif key == curses.KEY_DOWN:
            selected = min(max(0, len(rows) - 1), selected + 1)
        elif key == curses.KEY_UP:
            selected = max(0, selected - 1)
        elif (key in DETAIL_KEYS or key == curses.KEY_ENTER) and rows:
            # DWho owns the resize-safe, scrollable presentation primitive.
            screen.timeout(-1)
            try:
                view_details(screen, 'Read-only snapshot | q: back',
                             json.dumps(rows[selected], indent=2, ensure_ascii=True).splitlines())
            finally:
                screen.timeout(POLL_MS)


def run(create_observation):
    # Reject cron/pipes before importing curses, constructing clients or reading journals.
    from dwho.cli import require_terminal
    try:
        require_terminal('tui requires an interactive terminal on stdin and stdout')
        if os.environ.get('TERM', '') in ('', 'dumb'):
            raise ValueError('TERM is unavailable')
    except ValueError:
        print('tui requires an interactive terminal on stdin and stdout', file=sys.stderr)
        return 2
    try:
        import curses
    except ImportError:
        print('tui requires Python curses support', file=sys.stderr)
        return 2
    observation = create_observation()
    try:
        observation.start()
        return curses.wrapper(screen_loop, observation)
    except KeyboardInterrupt:
        return 130
    except (curses.error, OSError):
        print('tui terminal unavailable or disconnected', file=sys.stderr)
        return 2
    finally:
        observation.close()
