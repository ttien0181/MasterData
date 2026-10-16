"""Public read-only Drive inventory and manifests; never downloads customer rows.

Use the existing public-folder parser and path guard. Metadata publisher counts
are distinguished from locally reprofiled input counts and matching accuracy.
"""
import hashlib
import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
import yaml
from fetch_cluster_bundles import entries, target_path


def main():
    root = Path(__file__).resolve().parents[1]
    base = root / 'artifacts/advanced-m0-20261014'
    metadata = base / 'drive-metadata'
    cases = json.loads((base / 'drive-access.json').read_text(encoding='utf8'))['entries']
    inventory = json.loads((metadata / 'inventory.json').read_text(encoding='utf8'))
    cache = {}
    def list_folder(identifier):
        with requests.Session() as session:
            return entries(session, identifier)
    # Parallel reads are independent; no accounts, cookies or model APIs.
    with ThreadPoolExecutor(max_workers=4) as pool:
        identifiers = {cases[row['relative_path'].split('/')[0]] for row in inventory}
        for identifier, result in zip(sorted(identifiers), pool.map(list_folder, sorted(identifiers))):
            cache[identifier] = result
        scale_ids = {cache[cases[r['relative_path'].split('/')[0]]][r['relative_path'].split('/')[1]] for r in inventory}
        for identifier, result in zip(sorted(scale_ids), pool.map(list_folder, sorted(scale_ids))):
            cache[identifier] = result
        seed_ids = {cache[cache[cases[r['relative_path'].split('/')[0]]][r['relative_path'].split('/')[1]]]['seed-7'] for r in inventory}
        for identifier, result in zip(sorted(seed_ids), pool.map(list_folder, sorted(seed_ids))):
            cache[identifier] = result
    def manifest(row):
        name, scale, seed = row['relative_path'].split('/')
        identifier = cache[cache[cases[name]][scale]][seed]
        filename = Path(row['manifest']).name
        file_id = cache[identifier][filename]
        with requests.Session() as session:
            response = session.get('https://drive.google.com/uc', params={'export': 'download', 'id': file_id}, timeout=(5, 25))
            response.raise_for_status()
            if 'text/html' in response.headers.get('Content-Type', ''):
                raise ValueError('Manifest download returned HTML')
            content = response.content
        digest = hashlib.sha256(content).hexdigest()
        expected = row.get('manifest_sha256')
        path = target_path(metadata / 'manifests', row['manifest'])
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as h:
            h.write(content)
        m = yaml.safe_load(content) if filename.endswith('.yaml') else json.loads(content)
        return {'relative_path': row['relative_path'], 'case': row['case'],
                'manifest_local_path': path.relative_to(root).as_posix(),
                'manifest_sha256': digest, 'inventory_expected_sha256': expected,
                'manifest_hash_matches_inventory': digest == expected if expected else None,
                'declared_source_files': len(m.get('file_hashes', {})),
                'declared_extensions': dict(Counter(Path(p).suffix for p in m.get('file_hashes', {}))),
                'declared_records': m.get('actual_records', m.get('generated_records')),
                'declared_positive_pairs': m.get('positive_pairs', m.get('MATCH_pair_count')),
                'declared_nonmatch_pairs': m.get('sampled_forbidden_pairs', m.get('NON_MATCH_pair_count')),
                'realism': m.get('realism_level', m.get('parameter_provenance')),
                'calibration_status': m.get('calibration_status'),
                'sources': m.get('source_counts'), 'declared_missingness': m.get('missingness_by_field_source'),
                'publisher_validation': m.get('distribution_validation', m.get('generation_status')),
                'scope': 'manifest independently downloaded now; source rows not downloaded or reprofiled remotely'}
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(manifest, inventory))
    report = {'access': 'public unauthenticated HTTPS read', 'bundles': results,
              'bundle_count': len(results), 'case_count': len({r['case'] for r in results}),
              'publisher_declared_records_total': sum(r['declared_records'] for r in results),
              'publisher_declared_file_count': sum(r['declared_source_files'] for r in results),
              'remote_row_quality_reprofiled': False}
    (base / 'drive-inventory.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k != 'bundles'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
