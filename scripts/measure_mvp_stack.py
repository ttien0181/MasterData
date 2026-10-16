"""Sample the running Compose stack while TC1 is exercised in another terminal.

Container working sets cover all processes. API/worker RSS also includes their
children (including Splink/import attempts); neither is DuckDB's memory limit.
"""
import argparse
import json
import re
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path

RSS_PROBE = '''import json,psutil
rows=[]
for p in psutil.process_iter():
    try: rows.append({"pid":p.pid,"rss":p.memory_info().rss,"name":p.name()})
    except (psutil.NoSuchProcess,psutil.AccessDenied): pass
print(json.dumps(rows))'''


def command(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False)
    if result.returncode:
        # CLI stderr can contain local configuration details. Keep the artifact safe.
        raise RuntimeError('DOCKER_MEASUREMENT_UNAVAILABLE')
    return result.stdout.strip()


def memory_bytes(value):
    number, unit = re.fullmatch(r'([\d.]+)\s*([A-Za-z]+)', value.strip()).groups()
    scale = {'B': 1, 'kB': 1000, 'MB': 1000**2, 'GB': 1000**3,
             'KiB': 1024, 'MiB': 1024**2, 'GiB': 1024**3}[unit]
    return int(float(number) * scale)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--seconds', type=float, default=300)
    parser.add_argument('--interval', type=float, default=2)
    parser.add_argument('--target-bytes', type=int, default=2800000000)
    parser.add_argument('--output', default='artifacts/openrouter-migration-20261015/stack-memory.json')
    parser.add_argument('--stop-file', type=Path, help='Finish sampling cleanly when this marker exists')
    args = parser.parse_args()
    if args.seconds <= 0 or args.interval <= 0:
        parser.error('duration and interval must be positive')
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {'started_at': datetime.now(UTC).isoformat(), 'status': 'SAMPLING',
              'target_bytes': args.target_bytes, 'peak_stack_working_set_bytes': 0,
              'services': {}, 'samples': [], 'scope': 'sampled container working sets and process RSS; not host memory'}
    started = time.monotonic()
    try:
        for service in ('backend', 'worker', 'frontend'):
            container = command(['docker', 'compose', 'ps', '-q', service])
            if not container or '\n' in container:
                raise RuntimeError('EXPECTED_ONE_CONTAINER_PER_SERVICE')
            state = json.loads(command(['docker', 'inspect', '--format',
                '{"restarts":{{.RestartCount}},"oom":{{.State.OOMKilled}}}', container]))
            report['services'][service] = {'container': container, 'before': state, 'peak_working_set_bytes': 0,
                                           'peak_process_rss_bytes': 0}
        while time.monotonic() - started < args.seconds and not (args.stop_file and args.stop_file.exists()):
            sample = {'elapsed_seconds': time.monotonic() - started, 'services': {}}
            for service, row in report['services'].items():
                stat = json.loads(command(['docker', 'stats', '--no-stream', '--format', '{{json .}}', row['container']]))
                used = memory_bytes(stat['MemUsage'].split('/')[0])
                detail = {'working_set_bytes': used, 'cpu_percent': float(stat['CPUPerc'].rstrip('%'))}
                row['peak_working_set_bytes'] = max(used, row['peak_working_set_bytes'])
                if service != 'frontend':
                    processes = json.loads(command(['docker', 'exec', row['container'], 'python', '-c', RSS_PROBE]))
                    detail['processes'] = processes
                    rss = sum(p['rss'] for p in processes)
                    detail['process_rss_bytes'] = rss
                    row['peak_process_rss_bytes'] = max(rss, row['peak_process_rss_bytes'])
                sample['services'][service] = detail
            total = sum(s['working_set_bytes'] for s in sample['services'].values())
            sample['stack_working_set_bytes'] = total
            report['peak_stack_working_set_bytes'] = max(total, report['peak_stack_working_set_bytes'])
            report['samples'].append(sample)
            output.write_text(json.dumps(report, indent=2), encoding='utf-8')
            time.sleep(min(args.interval, max(0., args.seconds - (time.monotonic() - started))))
        for row in report['services'].values():
            row['after'] = json.loads(command(['docker', 'inspect', '--format',
                '{"restarts":{{.RestartCount}},"oom":{{.State.OOMKilled}}}', row['container']]))
        report['status'] = 'MEASURED'
        report['within_target'] = (report['peak_stack_working_set_bytes'] <= args.target_bytes
            and all(not r['after']['oom'] and r['after']['restarts'] == r['before']['restarts']
                    for r in report['services'].values()))
    except (RuntimeError, ValueError, KeyError) as error:
        report.update(status='UNAVAILABLE', error_code=str(error) if isinstance(error, RuntimeError) else type(error).__name__)
    finally:
        if not report['samples']:
            report['peak_stack_working_set_bytes'] = None
            for row in report['services'].values():
                row['peak_working_set_bytes'] = row['peak_process_rss_bytes'] = None
        report['elapsed_seconds'] = time.monotonic() - started
        output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: report[k] for k in ('status', 'peak_stack_working_set_bytes', 'elapsed_seconds')}))
    return 0 if report.get('within_target') else 1


if __name__ == '__main__':
    raise SystemExit(main())
