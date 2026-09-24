"""Render the existing YAML/Mako configuration independently of the CLI."""

import copy
import os
from collections import OrderedDict
import six
import yaml
from mako.template import Template
from monit_docker.domain.errors import MonitoringError

try:
    from yaml import CSafeLoader as YamlLoader, CSafeDumper as YamlDumper
except ImportError:
    from yaml import SafeLoader as YamlLoader, SafeDumper as YamlDumper

_TPL_IMPORTS = ('from os import environ as ENV',
                'from sonicprobe.helpers import to_yaml as my')
_CONFIG_SECTIONS = (('clients', 'client', 'config'),
                    ('ctn-groups', 'ctn-group', 'match'),
                    ('dir-groups', 'dir-group', 'paths'),
                    ('conditions', 'condition', 'expr'),
                    ('commands', 'command', 'exec'))


class Configuration(object):
    def __init__(self, conffile, inline=None):
        self.conffile = conffile
        self.inline = inline
        self._config_dir = ''
        self._common_conf = {'general': {}, 'vars': {}}

    @staticmethod
    def _load_yaml(stream, loader = YamlLoader):
        return yaml.load(stream, Loader = loader)

    @staticmethod
    def _dump_yaml(stream, dumper = YamlDumper, default_flow_style = True):
        return yaml.dump(stream, Dumper = dumper, default_flow_style = default_flow_style)

    def _import_conf_file(self, filepath, config_dir = None, xvars = None):
        if not xvars:
            xvars = {}

        if config_dir and not filepath.startswith(os.path.sep):
            filepath = os.path.join(config_dir, filepath)

        with open(filepath, 'r') as f:
            return self._load_yaml(
                Template(f.read(),
                         imports = _TPL_IMPORTS).render(**xvars))

    def _parse_import_file(self, conf, name, config_dir, xvars = None):
        r = OrderedDict()

        import_key = "@import_%s" % name

        if not conf.get(import_key):
            return r

        if isinstance(conf[import_key], six.string_types):
            c = [conf[import_key]]
        else:
            c = conf[import_key]

        for import_file in c:
            r.update(
                self._import_conf_file(
                    import_file,
                    config_dir,
                    xvars))

        return r

    def _render_conf_object(self, conf, xvars = None):
        if not xvars:
            xvars = {}

        return self._load_yaml(
            Template(self._dump_yaml(conf, default_flow_style = False),
                     imports = _TPL_IMPORTS).render(**xvars))

    def _load_conf_section(self, xtype, section, conf, config_dir = None):
        r = OrderedDict()

        if not config_dir:
            config_dir = self._config_dir

        xvars = copy.deepcopy(self._common_conf)
        xvars.update(self._parse_import_file(conf, 'vars', config_dir, xvars))

        r     = self._parse_import_file(conf, xtype, config_dir, xvars)
        r.update(conf)

        for name, value in six.iteritems(copy.copy(r)):
            if name.startswith('@'):
                del r[name]
                continue

            c = copy.deepcopy(self._common_conf)
            c.update(copy.deepcopy(xvars))
            c["%s_name" % xtype] = name
            c['vars'].update(copy.deepcopy(xvars['vars']))
            c['vars'].update(self._parse_import_file(value, 'vars', config_dir, c))

            if 'vars' in value:
                c['vars'].update(copy.deepcopy(value['vars']))

            if section in value:
                r[name][section] = self._render_conf_object(value[section], c)
            else:
                raise MonitoringError(110, "missing %s in %s: %r" % (section, xtype, name))

            for x in ('vars', '@import_vars'):
                if x in r[name]:
                    del r[name][x]

        return r

    def load(self, include_rules=True):
        self._common_conf = {'general': {}, 'vars': {}}
        self._config_dir = ''
        if os.path.exists(self.conffile):
            self._config_dir = os.path.dirname(os.path.abspath(self.conffile))
            with open(self.conffile, 'r') as stream:
                conf = self._load_yaml(stream)
        elif self.inline:
            conf = self._load_yaml(self.inline)
        else:
            return {}
        if not isinstance(conf, dict):
            raise MonitoringError(110, 'configuration must be a mapping')
        for key in ('general', 'vars'):
            if conf.get(key):
                self._common_conf[key] = dict(conf[key])
        result = {}
        for key, kind, section in _CONFIG_SECTIONS:
            if not include_rules and key in ('conditions', 'commands'):
                continue
            if conf.get(key):
                result[key] = self._load_conf_section(kind, section, conf[key])
        return result
