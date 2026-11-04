"""LiveScan breadboard-project storage: JSON documents plus captured images.

Breadboard projects live beside PCB reports but never touch KiCad inputs:
<reports>/.livescan/<project-id>/project.json and images/<file>.
All identifiers and filenames are strictly validated; nothing here runs AI.
"""
import json
import re
import secrets
from pathlib import Path

VERSION = 1
MAX_PROJECT_BYTES = 2 * 1024 * 1024
MAX_IMAGE_BYTES = 30 * 1024 * 1024
IMAGE_SUFFIXES = {'.png': 'image/png', '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.webp': 'image/webp'}

ID_PATTERN = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.\-]{0,60}')


def root(reports):
    folder = Path(reports) / '.livescan'
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def check_id(value):
    if not isinstance(value, str) or not ID_PATTERN.fullmatch(value):
        raise ValueError('Unknown breadboard project.')
    if value in ('.', '..'):
        raise ValueError('Unknown breadboard project.')
    return value


def check_image_name(value):
    if (not isinstance(value, str) or len(value) > 120 or '/' in value or '\\' in value
            or any(ord(c) < 32 for c in value)):
        raise ValueError('Invalid image name.')
    suffix = Path(value).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError('Images must be PNG, JPEG or WebP.')
    return value


def new_id():
    return 'ls-' + secrets.token_hex(4)


def validate_project(data):
    """Structural validation of a client-supplied project document."""
    if not isinstance(data, dict):
        raise ValueError('Invalid project document.')
    if data.get('version') != VERSION:
        raise ValueError('Unsupported project version.')
    for key in ('components', 'wires', 'images', 'questions'):
        if not isinstance(data.get(key), list):
            raise ValueError('Invalid project document.')
    if not isinstance(data.get('breadboard'), dict):
        raise ValueError('Invalid project document.')
    if data['breadboard'].get('size') not in ('full', 'half'):
        raise ValueError('Unknown breadboard size.')
    if len(json.dumps(data)) > MAX_PROJECT_BYTES:
        raise ValueError('Project document is too large.')
    if len(data['components']) > 500 or len(data['wires']) > 500 or len(data['images']) > 100:
        raise ValueError('Project has too many entries.')
    return data


def list_projects(reports):
    out = []
    folder = root(reports)
    for path in sorted(folder.iterdir()):
        if not path.is_dir() or not ID_PATTERN.fullmatch(path.name):
            continue
        try:
            data = json.loads((path / 'project.json').read_text())
            validate_project(data)
        except (OSError, ValueError):
            continue
        out.append({'id': data.get('id', path.name), 'name': data.get('name', 'Breadboard build'),
                    'updated': data.get('updated', ''),
                    'components': len(data['components']), 'wires': len(data['wires']),
                    'images': len(data['images'])})
    return sorted(out, key=lambda p: p['updated'], reverse=True)


def load_project(reports, project_id):
    check_id(project_id)
    path = root(reports) / project_id / 'project.json'
    try:
        data = json.loads(path.read_text())
    except OSError:
        raise ValueError('Unknown breadboard project.')
    return validate_project(data)


def save_project(reports, project_id, data):
    """Persist a validated document; returns the stored summary."""
    check_id(project_id)
    validate_project(data)
    if data.get('id') != project_id:
        raise ValueError('Project id mismatch.')
    folder = root(reports) / project_id
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / 'project.json.tmp'
    tmp.write_text(json.dumps(data, ensure_ascii=False))
    tmp.replace(folder / 'project.json')
    return {'id': project_id, 'updated': data.get('updated', '')}


def delete_project(reports, project_id):
    check_id(project_id)
    folder = root(reports) / project_id
    if not folder.is_dir():
        raise ValueError('Unknown breadboard project.')
    for path in sorted(folder.rglob('*'), reverse=True):
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.is_dir():
            path.rmdir()
    folder.rmdir()
    return {'id': project_id, 'deleted': True}


def save_image(reports, project_id, name, body):
    check_id(project_id)
    check_image_name(name)
    if not body or len(body) > MAX_IMAGE_BYTES:
        raise ValueError('Image is empty or too large.')
    folder = root(reports) / project_id
    if not folder.is_dir():
        raise ValueError('Unknown breadboard project.')
    images = folder / 'images'
    images.mkdir(exist_ok=True)
    target = images / name
    if target.is_symlink():
        raise ValueError('Invalid image name.')
    # Keep original captures; never silently overwrite a different shot.
    if target.exists():
        stem, suffix = name.rsplit('.', 1)
        for n in range(2, 1000):
            candidate = images / f'{stem}-{n}.{suffix}'
            if not candidate.exists():
                target = candidate
                name = candidate.name
                break
        else:
            raise ValueError('Too many images with this name.')
    target.write_bytes(body)
    return {'name': name, 'size': len(body)}


def load_image(reports, project_id, name):
    check_id(project_id)
    check_image_name(name)
    path = root(reports) / project_id / 'images' / name
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError('Image not found.')
    suffix = Path(name).suffix.lower()
    return path.read_bytes(), IMAGE_SUFFIXES[suffix]
