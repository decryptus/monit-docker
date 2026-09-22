"""Application failures, independent of process exit and Docker exceptions."""


class MonitoringError(Exception):
    def __init__(self, code, message):
        super(MonitoringError, self).__init__(message)
        self.code = code


class RuleSyntaxError(SyntaxError):
    pass


class ResourceTypeError(TypeError):
    pass
