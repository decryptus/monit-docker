#!/usr/bin/env python3
"""Trace the existing logo and icon into standalone SVG paths."""

import argparse
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
from PIL import Image
import potrace


ROOT = Path(__file__).resolve().parent
SOURCES = (
    ('monit-docker-logo', 'monit-docker horizontal logo'),
    ('monit-docker-icon', 'monit-docker square icon'),
)
BODY_COLOR = '#112944'
ACCENT_COLOR = '#3898fd'
ALPHA_THRESHOLD = 128
ACCENT_BLUE_DIFFERENCE = 100
ACCENT_GREEN_DIFFERENCE = 60
TRACE_OPTIONS = {
    'turdsize': 8,
    'alphamax': 1.334,
    'opticurve': True,
    'opttolerance': 0.2,
}
SVG_NAMESPACE = 'http://www.w3.org/2000/svg'


def point(value):
    return '%.2f %.2f' % (value.x, value.y)


def trace(mask):
    # Bitmap inverts its input: False denotes the foreground before conversion.
    curves = potrace.Bitmap(~mask).trace(**TRACE_OPTIONS)
    commands = []
    for curve in curves:
        commands.append('M' + point(curve.start_point))
        for segment in curve:
            if segment.is_corner:
                commands.append('L' + point(segment.c) + ' ' + point(segment.end_point))
            else:
                commands.append('C' + point(segment.c1) + ' ' + point(segment.c2)
                                + ' ' + point(segment.end_point))
        commands.append('Z')
    if not commands:
        raise ValueError('Source contains no traceable foreground')
    return ' '.join(commands)


def vector(source, title):
    with Image.open(source) as image:
        pixels = np.asarray(image.convert('RGBA'), dtype=np.int16)
        width, height = image.size
    body = pixels[:, :, 3] >= ALPHA_THRESHOLD
    accent = (body
              & (pixels[:, :, 2] - pixels[:, :, 0] > ACCENT_BLUE_DIFFERENCE)
              & (pixels[:, :, 1] - pixels[:, :, 0] > ACCENT_GREEN_DIFFERENCE))
    root = ET.Element('svg', {
        'xmlns': SVG_NAMESPACE,
        'width': str(width),
        'height': str(height),
        'viewBox': '0 0 %d %d' % (width, height),
        'role': 'img',
        'aria-label': title,
    })
    ET.SubElement(root, 'title').text = title
    ET.SubElement(root, 'desc').text = (
        'Vector tracing of the existing raster artwork. '
        'Transparent background; lettering outlined as paths; two solid colors.'
    )
    for mask, color in ((body, BODY_COLOR), (accent, ACCENT_COLOR)):
        ET.SubElement(root, 'path', {
            'fill': color,
            'fill-rule': 'evenodd',
            'd': trace(mask),
        })
    ET.indent(root, space='  ')
    return ET.tostring(root, encoding='unicode') + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check SVG files without writing')
    args = parser.parse_args()
    mismatches = []
    for stem, title in SOURCES:
        content = vector(ROOT / (stem + '.png'), title)
        target = ROOT / (stem + '.svg')
        if args.check:
            if not target.is_file() or target.read_text(encoding='utf-8') != content:
                mismatches.append(target.name)
        else:
            target.write_text(content, encoding='utf-8')
    if mismatches:
        parser.exit(1, 'Outdated vectors: ' + ', '.join(mismatches) + '\n')
    print('%d SVG files %s.' % (len(SOURCES), 'verified' if args.check else 'generated'))


if __name__ == '__main__':
    main()
