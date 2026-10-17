"""Retry frozen export through HTTP; credentials are read locally, never logged."""
import argparse
import csv
import hashlib
import json
import time
from pathlib import Path

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run', required=True)
    parser.add_argument('--credentials', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--records', type=int, default=9992)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    report = {'run_id': args.run, 'status': 'FAIL'}
    started = time.monotonic()
    def checked(response):
        if response.is_error:
            raise RuntimeError(f'HTTP_{response.status_code}')
        return response
    try:
        with httpx.Client(base_url='http://localhost:8001', timeout=600,
                          headers={'Origin': 'http://localhost:3000'}) as client:
            login = checked(client.post('/api/v1/auth/login', json=json.loads(
                args.credentials.read_text(encoding='utf-8')))).json()
            client.headers['X-CSRF-Token'] = login['csrf_token']
            base = f'/api/v1/mdm/runs/{args.run}'
            preview_start = time.monotonic()
            preview = checked(client.post(base + '/previews', json={
                'choices': {}, 'acknowledge_unreviewed': False})).json()
            report['preview_seconds'] = time.monotonic() - preview_start
            export_start = time.monotonic()
            path = args.output / 'golden.csv'
            with client.stream('POST', base + f"/previews/{preview['preview_id']}/execute",
                               json={'execute_request_id': 'hotfix-export:' + preview['preview_id']}) as response:
                checked(response)
                export_id = response.headers['X-Export-Id']
                with path.open('wb') as stream:
                    for chunk in response.iter_bytes():
                        stream.write(chunk)
            report['export_download_seconds'] = time.monotonic() - export_start
            covered, rows = set(), 0
            with path.open(encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                assert reader.fieldnames == ['master_record_id', 'full_name', 'phone_number', 'email_address',
                    'raw_address', 'source_record_ids', 'source_systems', 'record_count', 'resolution_status', 'review_pending']
                for row in reader:
                    members = row['source_record_ids'].split(';')
                    assert len(members) == int(row['record_count']) and not covered.intersection(members)
                    covered.update(members)
                    rows += 1
            assert len(covered) == args.records and rows == preview['estimated_output_rows']
            def file_hash():
                checksum = hashlib.sha256()
                with path.open('rb') as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b''):
                        checksum.update(chunk)
                return checksum.hexdigest()
            checksum = file_hash()
            with client.stream('GET', base + f'/exports/{export_id}/download') as response:
                checked(response)
                history = hashlib.sha256()
                for chunk in response.iter_bytes():
                    history.update(chunk)
            assert history.hexdigest() == checksum
            repeated = checked(client.post(base + f"/previews/{preview['preview_id']}/execute",
                json={'execute_request_id': 'hotfix-export:' + preview['preview_id']}))
            assert hashlib.sha256(repeated.content).hexdigest() == checksum
            report.update(status='PASS', export_id=export_id, rows=rows, covered_records=len(covered),
                          csv_sha256=checksum, bytes=path.stat().st_size, download_replay='PASS')
    except Exception as error:
        report['error_type'] = type(error).__name__
        if isinstance(error, RuntimeError):
            report['error_code'] = str(error)
        raise
    finally:
        report['total_seconds'] = time.monotonic() - started
        (args.output / 'export-retry.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps(report))


if __name__ == '__main__':
    main()
