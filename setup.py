#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import yaml
from setuptools import find_packages, setup

current_dir            = os.path.abspath(os.path.dirname(__file__))
requirements           = [line.strip() for line in open(os.path.join(current_dir, 'requirements.txt'), 'r').readlines()]
setup_config           = os.path.join(current_dir, 'setup.yml')
readme_file            = os.path.join(current_dir, 'README.md')
long_desc              = None
long_desc_content_type = None

if os.path.isfile(setup_config):
    setup_cfg = yaml.safe_load(open(setup_config, 'r').read())

if os.path.isfile(readme_file):
    long_desc = open(readme_file, 'r').read()
    long_desc_content_type = 'text/markdown'

setup(
    name                          = setup_cfg['name'],
    version                       = setup_cfg['version'],
    description                   = setup_cfg['description'],
    author                        = setup_cfg['author'],
    author_email                  = setup_cfg['author_email'],
    license                       = setup_cfg['license'],
    url                           = setup_cfg['url'],
    scripts                       = ['bin/monit-docker'],
    install_requires              = requirements,
    python_requires               = ', '.join(setup_cfg['python_requires']),
    classifiers                   = setup_cfg['classifiers'],
    long_description              = long_desc,
    long_description_content_type = long_desc_content_type
)
