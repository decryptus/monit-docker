"""Internal rule and cycle values; these are not a public wire protocol."""

from collections import namedtuple


Condition = namedtuple('Condition', 'resource operator value pre_operator pre_value source')
Action = namedtuple('Action', 'kind command args kwargs')
ActionResult = namedtuple('ActionResult', 'container_id rule command success')
ActionDecision = namedtuple('ActionDecision', 'container_id rule command status')
CycleResult = namedtuple('CycleResult', 'snapshots actions')


class Rule(namedtuple('RuleBase', 'source conditions actions')):
    __slots__ = ()

    @property
    def resources(self):
        return tuple(dict.fromkeys(c.resource for c in self.conditions))

    @property
    def needs_metrics(self):
        return any(r not in ('pid', 'status') for r in self.resources)
