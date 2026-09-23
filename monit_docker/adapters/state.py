"""Local Unix process lock and atomic JSON rule state (stdlib only)."""

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
        self.lock_pid = None
        self.entries = {}
        self.observations = {}
        self.version = 1

    def __enter__(self):
        import fcntl
        # Reject re-entry before replacing/closing the outer context's descriptor.
        if self.lock_fd is not None:
            raise RuntimeError('state is already locked by this instance')
        try:
            try:
                os.makedirs(self.directory, 0o700)
            except OSError as error:
                if error.errno != errno.EEXIST:
                    raise
            flags = os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
            self.lock_fd = os.open(self.path + '.lock', flags, 0o600)
            self.lock_pid = os.getpid()
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
            self.lock_pid = None

    def _require_lock(self):
        if self.lock_fd is None:
            raise RuntimeError('state must be locked before use')
        if self.lock_pid != os.getpid():
            raise RuntimeError('state lock cannot be reused after fork')

    def __exit__(self, *exc):
        self._close()
        # Never unlink the lock: waiting/new processes must see the same inode.

    def _load(self):
        self.entries = {}
        self.observations = {}
        self.version = 1
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
        if (not isinstance(data, dict) or type(data.get('version')) is not int
                or data['version'] not in (1, 2)
                or set(data) != ({'version', 'cooldowns'} if data['version'] == 1
                                 else {'version', 'cooldowns', 'observations'})
                or not isinstance(data['cooldowns'], dict)):
            raise ValueError('unsupported state schema')
        for key, value in data['cooldowns'].items():
            if not self._valid_key(key) or not self._valid_time(value):
                raise ValueError('invalid cooldown entry')
        self.entries = data['cooldowns']
        if data['version'] == 2:
            self.observations = self._validated_observations(data['observations'])
        self.version = data['version']

    @staticmethod
    def _valid_key(key):
        return isinstance(key, str) and re.fullmatch(r'[0-9a-f]{64}', key) is not None

    @staticmethod
    def _valid_time(value):
        try:
            return (not isinstance(value, bool) and isinstance(value, (int, float))
                    and math.isfinite(value) and value >= 0)
        except OverflowError:
            return False

    @staticmethod
    def _validated_observations(observations):
        if not isinstance(observations, dict):
            raise ValueError('invalid observations')
        validated = {}
        for key, value in observations.items():
            if (not LocalState._valid_key(key)
                    or not isinstance(value, list) or len(value) != 2):
                raise ValueError('invalid observation entry')
            pair = list(value)
            valid = all(LocalState._valid_time(v) for v in pair)
            if not valid or pair[0] > pair[1]:
                raise ValueError('invalid observation entry')
            validated[key] = pair
        return validated

    def replace_observations(self, observations, read_only=False):
        self._require_lock()
        # Validate and detach caller-owned lists before comparing or saving,
        # including previews: invalid input must never alter disk or memory.
        observations = self._validated_observations(observations)
        if observations == self.observations:
            return
        if not read_only:
            self._save(self.entries, observations)
        self.observations = observations
        self.version = 2

    def reserve(self, key, now, seconds, read_only=False):
        self._require_lock()
        if not self._valid_key(key):
            raise ValueError('invalid cooldown key')
        if (not self._valid_time(now) or not self._valid_time(seconds)
                or not self._valid_time(now + seconds)):
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

    def _save(self, entries, observations=None):
        temporary = None
        try:
            data = {'version': self.version, 'cooldowns': entries}
            if observations is not None or self.version == 2:
                data.update(version=2, observations=(self.observations if observations is None
                                                     else observations))
            fd, temporary = tempfile.mkstemp(prefix='.monit-state-', dir=self.directory)
            with os.fdopen(fd, 'w') as stream:
                json.dump(data, stream, sort_keys=True, allow_nan=False)
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
