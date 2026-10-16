"""One synthetic, paid API canary with durable receipts and a $0.001 ceiling."""
import argparse
import json
from pathlib import Path

from dotenv import load_dotenv


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--env-file', default='.env.qa')
    parser.add_argument('--output', default='artifacts/openrouter-migration-20261015/api-canary.json')
    args = parser.parse_args()
    load_dotenv(args.env_file, override=True)
    from masterone.config import get_settings
    from masterone.mdm.matching_agents.encoder_accounting import encode_accounted
    from masterone.mdm.matching_agents.openrouter_embedding import OpenRouterEmbeddingRuntime, runtime_spec
    settings = get_settings()
    settings.embedding_max_requests_per_run = 1
    settings.embedding_max_retries = 0
    settings.embedding_budget_usd = .001
    identity, budget = runtime_spec()
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    report = {'scope': 'Synthetic API canary only; no dataset quality or stack RAM claim',
              'model': identity, 'status': 'PENDING', 'budget_usd': .001, 'receipts': []}

    def save():
        temporary = target.with_suffix('.tmp')
        temporary.write_text(json.dumps(report, indent=2), encoding='utf-8')
        temporary.replace(target)

    def receipt(row):
        report['receipts'].append(row)
        save()

    save()
    try:
        with OpenRouterEmbeddingRuntime(identity, budget) as runtime:
            vectors = encode_accounted(runtime, ['Synthetic Example Alpha', 'Synthetic Example Beta'], 'name', receipt)
            report.update(status='OK', dimensions=[len(v) for v in vectors], usage=runtime.usage,
                          calls=runtime.calls, retries=runtime.retry_count)
    except Exception as error:
        report.update(status='FAILED', error_code=str(error) if str(error).startswith('EMBEDDING_') else type(error).__name__)
    save()
    print(json.dumps({k: report[k] for k in ('status', 'scope', 'model')}, ensure_ascii=True))
    return 0 if report['status'] == 'OK' else 1


if __name__ == '__main__':
    raise SystemExit(main())
