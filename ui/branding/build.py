#!/usr/bin/env python3
"""Export the existing raster artwork without redrawing or enlarging it."""

import argparse
from io import BytesIO
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parent
LOGO_WIDTHS = (320, 640, 1280)
ICON_SIZES = (16, 32, 48, 64, 128, 180, 192, 256)
ICO_SIZES = (16, 32, 48, 64, 128, 256)


def encode(image, image_format, **options):
    output = BytesIO()
    image.save(output, format=image_format, **options)
    return output.getvalue()


def exports():
    with Image.open(ROOT / 'monit-docker-logo.png') as source:
        logo = source.convert('RGBA')
    with Image.open(ROOT / 'monit-docker-icon.png') as source:
        icon = source.convert('RGBA')
    for width in LOGO_WIDTHS:
        if width > logo.width:
            raise ValueError('Logo exports must not enlarge the source')
        size = (width, round(logo.height * width / logo.width))
        resized = logo.resize(size, Image.Resampling.LANCZOS)
        stem = 'logos/monit-docker-logo-%d' % width
        yield stem + '.png', encode(resized, 'PNG', optimize=True)
        yield stem + '.webp', encode(resized, 'WEBP', lossless=True, method=6)
        white = Image.new('RGB', size, 'white')
        white.paste(resized, mask=resized.getchannel('A'))
        yield stem + '-white.jpg', encode(white, 'JPEG', quality=95, subsampling=0, optimize=True)
    for size in ICON_SIZES:
        if size > min(icon.size):
            raise ValueError('Icon exports must not enlarge the source')
        resized = icon.resize((size, size), Image.Resampling.LANCZOS)
        yield 'icons/monit-docker-icon-%d.png' % size, encode(resized, 'PNG', optimize=True)
    yield 'icons/monit-docker-icon-256.webp', encode(icon, 'WEBP', lossless=True, method=6)
    yield 'icons/favicon.ico', encode(icon, 'ICO', sizes=[(size, size) for size in ICO_SIZES])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Check exports without writing files')
    args = parser.parse_args()
    mismatches = []
    count = 0
    for relative, content in exports():
        path = ROOT / relative
        count += 1
        if args.check:
            if not path.is_file() or path.read_bytes() != content:
                mismatches.append(relative)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    if mismatches:
        parser.exit(1, 'Outdated exports: ' + ', '.join(mismatches) + '\n')
    print('%d branding exports %s.' % (count, 'verified' if args.check else 'generated'))


if __name__ == '__main__':
    main()
