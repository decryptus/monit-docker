"""Local Unix process lock and atomic JSON cooldown storage (stdlib only)."""

import errno
import json
import math
import os
import re
import stat
import tempfile

from monit_docker.domain.errors import MonitoringError


class LocalState(object):
    def __init__(self, path):
        # Resolve directory aliases so they share the same adjacent lock file.
        absolute = os.path.abspath(path)
        self.directory = os.path.realpath(os.path.dirname(absolute))
        self.path = os.path.join(self.directory, os.path.basename(absolute))
        self.lock_fd = None
        self.entries = {}

    def __enter__(self):
        import fcntl
        try:
            try:
                os.makedirs(self.directory, 0o700)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
            self.lock_fd = os.open(self.path + '.lock', flags, 0o600)
            if not stat.S_ISREG(os.fstat(self.lock_fd).st_mode):
                raise ValueError('lock must be a regular file')
            try:
                fcntl.flock(self.lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                if error.errno in (errno.EACCES, errno.EAGAIN):
                    raise MonitoringError(117, 'another cycle holds the state lock: %s' % self.path)
                raise
            self._load()
            return self
        except BaseException as error:
            self._close()
            if isinstance(error, MonitoringError) or not isinstance(error, Exception):
                raise
            raise MonitoringError(118, 'cannot open cron state %s: %s' % (self.path, error))

    def _close(self):
        if self.lock_fd is not None:
            os.close(self.lock_fd)
            self.lock_fd = None

    def __exit__(self, *exc):
        self._close()
        # Never unlink the lock: waiting/new processes must see the same inode.

    def _load(self):
        self.entries = {}
        try:
            fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, 'O_NOFOLLOW', 0))
        except OSError as error:
            if error.errno == errno.ENOENT:
                return
            raise
        with os.fdopen(fd, 'r') as stream:
            if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                raise ValueError('state must be a regular file')
            data = json.load(stream)
        if (not isinstance(data, dict) or set(data) != {'version', 'cooldowns'}
                or type(data['version']) is not int or data['version'] != 1
                or not isinstance(data['cooldowns'], dict)):
            raise ValueError('unsupported state schema')
        for key, value in data['cooldowns'].items():
            if (not re.fullmatch(r'[0-9a-f]{64}', key) or isinstance(value, bool)
                    or not isinstance(value, (int, float)) or not math.isfinite(value)
                    or value < 0):
                raise ValueError('invalid cooldown entry')
        self.entries = data['cooldowns']

    def reserve(self, key, now, seconds, read_only=False):
        if self.lock_fd is None:
            raise RuntimeError('state must be locked before use')
        if (not math.isfinite(now) or now < 0 or not math.isfinite(seconds)
                or seconds < 0 or not math.isfinite(now + seconds)):
            raise ValueError('invalid cooldown time')
        if self.entries.get(key, 0) > now:
            return False
        entries = dict((k, v) for k, v in self.entries.items() if v > now)
        entries[key] = now + seconds
        if not read_only:
            self._save(entries)
        # Dry runs model reservations within this cycle without writing state.
        self.entries = entries
        return True

    def _save(self, entries):
        temporary = None
        try:
            fd, temporary = tempfile.mkstemp(prefix='.monit-state-', dir=self.directory)
            with os.fdopen(fd, 'w') as stream:
                json.dump({'version': 1, 'cooldowns': entries}, stream,
                          sort_keys=True, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(temporary, self.path)
            temporary = None
            directory_fd = os.open(self.directory, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (OSError, ValueError, TypeError) as error:
            raise MonitoringError(118, 'cannot save cron state %s: %s' % (self.path, error))
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass  # Preserve the original write error.
