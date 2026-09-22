"""Application failures, independent of process exit and Docker exceptions."""

from numbers import Integral


class MonitoringError(Exception):
    def __init__(self, code, message):
        super(MonitoringError, self).__init__(message)
        self.code = code


class CommandExecutionError(MonitoringError):
    """A completed exec failed; preserve its status separately from agent code 116."""

    def __init__(self, exit_code, message):
        if isinstance(exit_code, bool) or not isinstance(exit_code, Integral) or not 1 <= exit_code <= 255:
            raise ValueError('command failure exit code must be an integer from 1 to 255')
        super(CommandExecutionError, self).__init__(116, message)
        self.exit_code = int(exit_code)


class RuleSyntaxError(SyntaxError):
    pass


class ResourceTypeError(TypeError):
    pass
