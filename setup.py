#!/usr/bin/env python
# -*- coding: UTF-8 -*-

import os
from setuptools import find_packages, setup

version                = '0.0.26'

current_dir            = os.path.abspath(os.path.dirname(__file__))
requirements           = [line.strip() for line in open(os.path.join(current_dir, 'requirements.txt'), 'r').readlines()]
version_file           = os.path.join(current_dir, 'VERSION')
readme_file            = os.path.join(current_dir, 'README.md')
long_desc              = None
long_desc_content_type = None

if os.path.isfile(version_file):
    version = open(version_file, 'r').readline().strip() or version

if os.path.isfile(readme_file):
    long_desc = open(readme_file, 'r').read()
    long_desc_content_type = 'text/markdown'

setup(
    name                          = 'monit-docker',
    version                       = version,
    description                   = 'monit-docker',
    author                        = 'Adrien Delle Cave',
    author_email                  = 'pypi@doowan.net',
    license                       = 'License GPL-2',
    url                           = 'https://github.com/decryptus/monit-docker',
    scripts                       = ['bin/monit-docker'],
    packages                      = find_packages(),
    install_requires              = requirements,
    python_requires               = '>=2.7, !=3.0.*, !=3.1.*, !=3.2.*, !=3.3.*, !=3.4.*',
    classifiers                   = ['License :: OSI Approved :: GNU General Public License v2 (GPLv2)',
                                     'Natural Language :: English',
                                     'Operating System :: Unix',
                                     'Programming Language :: Python',
                                     'Programming Language :: Python :: 2',
                                     'Programming Language :: Python :: 2.7',
                                     'Programming Language :: Python :: 3',
                                     'Programming Language :: Python :: 3.5',
                                     'Programming Language :: Python :: 3.6',
                                     'Programming Language :: Python :: 3.7',
                                     'Topic :: System :: Monitoring',
                                     'Topic :: Terminals',
                                     'Topic :: Utilities'],
    long_description              = long_desc,
    long_description_content_type = long_desc_content_type
)
