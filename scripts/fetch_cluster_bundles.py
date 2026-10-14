"""Fetch one original public Drive bundle and verify every declared SHA-256."""
import argparse
import hashlib
import html
import json
import re
from pathlib import Path, PurePosixPath, PureWindowsPath

import requests
import yaml

ROOT_FOLDER = '1ZSRF1p6OY9hCglV95jebNcOZusQ_D1Ws'
ENTRY = re.compile(r'aria-label="([^"]+)"[^>]*ssk=\'5:auSv138:([A-Za-z0-9_-]{33})-')


def target_path(root, relative):
    part = PurePosixPath(relative)
    if ('\\' in relative or part.is_absolute() or PureWindowsPath(relative).drive
            or '..' in part.parts or not part.parts):
        raise ValueError('UNSAFE_MANIFEST_PATH')
    destination = Path(root).resolve().joinpath(*part.parts)
    if not destination.resolve().is_relative_to(Path(root).resolve()):
        raise ValueError('UNSAFE_MANIFEST_PATH')
    return destination


def checksum(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        while chunk := stream.read(1024**2):
            digest.update(chunk)
    return digest.hexdigest()


def verify_existing(path, expected):
    if checksum(path) != expected:
        raise ValueError('INPUT_HASH_MISMATCH: ' + str(path))


def entries(session, folder):
    response = session.get('https://drive.google.com/drive/folders/' + folder, timeout=30)
    response.raise_for_status()
    found = {}
    for label, identifier in ENTRY.findall(response.text):
        name = re.sub(r' (?:Shared folder|[^ ]+ Shared)$', '', html.unescape(label))
        if name in found and found[name] != identifier:
            raise ValueError('AMBIGUOUS_DRIVE_ENTRY')
        found[name] = identifier
    if not found:
        raise ValueError('DRIVE_FOLDER_UNAVAILABLE')
    return found


def download(session, identifier, path, expected=None):
    if path.exists() and expected:
        verify_existing(path, expected)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.owned-download-part')
    response = session.get('https://drive.google.com/uc', params={'export': 'download', 'id': identifier},
                           timeout=(15, 60), stream=True)
    response.raise_for_status()
    if 'text/html' in response.headers.get('Content-Type', ''):
        raise ValueError('DRIVE_DOWNLOAD_REQUIRES_CONFIRMATION: ' + identifier)
    try:
        with temporary.open('xb') as stream:
            for chunk in response.iter_content(1024**2):
                stream.write(chunk)
        if expected:
            verify_existing(temporary, expected)
        if path.exists():
            if checksum(path) != checksum(temporary):
                raise ValueError('CHANGED_EXISTING_SOURCE: ' + str(path))
            temporary.unlink()
        else:
            temporary.replace(path)
    finally:
        response.close()


def fetch(root_folder, case, scale, output):
    with requests.Session() as session:
        folders = entries(session, root_folder)
        case_id = folders[case]
        scales = entries(session, case_id)
        if scale != min(scales, key=lambda s: {'10k': 10000, '100k': 100000, '1m': 1000000}.get(s, float('inf'))):
            raise ValueError('ONLY_SMALLEST_BUNDLE_AUTHORIZED')
        seed = entries(session, scales[scale])['seed-7']
        cache = {}
        def resolve(relative):
            folder = seed
            for part in PurePosixPath(relative).parts:
                if folder not in cache:
                    cache[folder] = entries(session, folder)
                folder = cache[folder][part]
            return folder
        output = Path(output) / case / scale / 'seed-7'
        manifest_path = target_path(output, 'manifest.yaml')
        download(session, resolve('manifest.yaml'), manifest_path)
        manifest = yaml.safe_load(manifest_path.read_text(encoding='utf-8'))
        hashes = manifest['file_hashes']
        rows = []
        for relative, expected in hashes.items():
            destination = target_path(output, relative)
            download(session, resolve(relative), destination, expected)
            rows.append({'path': relative, 'sha256': checksum(destination), 'bytes': destination.stat().st_size})
        report = {'case': case, 'scale': scale, 'seed': 7, 'actual_records': manifest['actual_records'],
                  'manifest_sha256': checksum(manifest_path), 'files': rows, 'verified': len(rows),
                  'complete': len(rows) == len(hashes)}
        (output / 'fetch-report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root-folder-id', default=ROOT_FOLDER)
    parser.add_argument('--case', default='TC1_easy_match')
    parser.add_argument('--scale', default='10k')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    report = fetch(args.root_folder_id, args.case, args.scale, args.output)
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}, indent=2), flush=True)


if __name__ == '__main__':
    main()
