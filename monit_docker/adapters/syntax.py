"""Legacy CLI rule grammar and resource names."""

import re

DOCKER_COMMANDS         = ('start',
                           'stop',
                           'remove',
                           'reload',
                           'restart',
                           'kill',
                           'pause',
                           'unpause')

RESOURCE_CHOICES        = ('mem_usage',
                           'mem_limit',
                           'mem_percent',
                           'cpu_percent',
                           'io_read',
                           'io_write',
                           'net_tx',
                           'net_rx',
                           'status',
                           'pid')

STATUS_RC               = {'running': 0,
                           'created': 10,
                           'paused': 20,
                           'restarting': 30,
                           'removing': 40,
                           'exited': 50,
                           'dead': 60}

DATATYPES               = RESOURCE_CHOICES
DATATYPES_BEFORE_RUN    = ('pid',
                           'status',)

PRE_COND_RE             = (r'(?:\s*(?P<pre_value>[0-9]+(?:\.[0-9]+)?\s*(?P<pre_value_unit>[a-zA-Z]+)?)\s+' +
                           r'(?P<pre_op>[\!\<\>=]=|[\<\>])\s+)?\s*')
DATATYPE_RE             = r'(?P<datatype>[a-z_]+(?:\[[a-zA-Z][a-zA-Z0-9_.-]{0,64}\])?)\s*'
OP_RE                   = r'(?P<op>[\!\<\>=]=|[\<\>]|\s+in\s+|\s+not in\s+)\s*'
VALUE_RE                = r'(?P<value>(?:[0-9]+(?:\.[0-9]+)?\s*(?P<value_unit>[a-zA-Z]+)?|[a-z]+|\((?:[a-z]+\,?){1,64}\)))'
CMD_RE                  = r'(?P<cmd>[^@].{2,})'
CMD_ALIAS_RE            = r'@(?P<cmd_alias>[a-zA-Z][a-zA-Z0-9_\-\.]{0,64})'
COND_ALIAS_RE           = r'@(?P<cond_alias>[a-zA-Z][a-zA-Z0-9_\-\.]{0,64})'

COND_MATCH              = re.compile(r'^' + PRE_COND_RE + DATATYPE_RE + OP_RE + VALUE_RE + r'\s*$').match

CMD_MATCH               = re.compile(r'^\s*' + CMD_RE + r'\s*$').match
CTN_GRP_MATCH           = re.compile(r'^(?P<subset>id|image|name|label)\s*:\s*(?P<pattern>.+)\s*$').match

EXPR_MATCH              = re.compile(r'^(?:(?:(?P<cond>' + PRE_COND_RE + DATATYPE_RE + OP_RE + VALUE_RE + r')\s*|' +
                                     r'\s*' + COND_ALIAS_RE + r')\s*' +
                                     r'\?)?\s*(?:' + CMD_ALIAS_RE + r'|' + CMD_RE + r')\s*$').match
