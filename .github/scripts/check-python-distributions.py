"""Reject mismatched Python release artifacts before either registry publishes."""

import ast
from email.parser import BytesParser
from pathlib import Path
import re
import tarfile
import zipfile


def check_distributions(root):
    root = Path(root)
    version = (root / 'VERSION').read_text().strip()
    if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version):
        raise ValueError('VERSION must be X.Y.Z')
    if (root / 'RELEASE').read_text().strip() != version:
        raise ValueError('RELEASE must match VERSION')
    # Do not import application/build dependencies just to check the version.
    tree = ast.parse((root / 'monit_docker' / '__init__.py').read_text())
    versions = [ast.literal_eval(node.value) for node in tree.body
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Name) and target.id == '__version__'
                        for target in node.targets)]
    if versions != [version]:
        raise ValueError('package __version__ must match VERSION')
    files = sorted((root / 'dist').iterdir())
    wheels = [path for path in files if path.suffix == '.whl']
    sources = [path for path in files if path.name.endswith('.tar.gz')]
    if len(wheels) != 1 or len(sources) != 1 or len(files) != 2:
        raise ValueError('expected exactly one wheel and one source distribution')
    with zipfile.ZipFile(wheels[0]) as archive:
        _check_paths(archive.namelist())
        metadata = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1:
            raise ValueError('wheel must have exactly one METADATA file')
        _check_metadata(archive.read(metadata[0]), version)
    with tarfile.open(sources[0], 'r:gz') as archive:
        _check_paths(archive.getnames())
        metadata = [member for member in archive.getmembers()
                    if member.name.count('/') == 1 and member.name.endswith('/PKG-INFO')]
        if len(metadata) != 1:
            raise ValueError('source distribution must have exactly one root PKG-INFO file')
        with archive.extractfile(metadata[0]) as stream:
            _check_metadata(stream.read(), version)
    return version


def _check_paths(names):
    for name in names:
        if 'notifications.local' in name.split('/') or name.endswith('/.env'):
            raise ValueError('private monitoring configuration must not be packaged')


def _check_metadata(data, version):
    metadata = BytesParser().parsebytes(data)
    name = re.sub(r'[-_.]+', '-', metadata.get('Name', '')).lower()
    if name != 'monit-docker' or metadata.get('Version') != version:
        raise ValueError('distribution name/version must match monit-docker %s' % version)


if __name__ == '__main__':
    print('Validated monit-docker %s distributions' % check_distributions(Path.cwd()))
