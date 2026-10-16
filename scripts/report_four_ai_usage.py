"""Read run-scoped durable accounting; never dump prompts, responses or secrets."""
import json
import re
import subprocess
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/four-ai-20261015'


def main():
    live = json.loads((OUT / 'live-run.json').read_text(encoding='utf8'))
    runs = [live['run_id'], *live.get('supplementary_run_ids', [])]
    assert all(re.fullmatch(r'[0-9a-f-]{36}', run) for run in runs)
    code = '''
import json
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from masterone.config import get_settings
from masterone.mdm.models import LLMRequest,LLMReservation
from masterone.mdm.workflow_models import WorkflowArtifact
runs=RUN_IDS
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    calls=[]
    for row in db.query(LLMRequest).join(LLMReservation,LLMRequest.request_hash==LLMReservation.id).filter(LLMReservation.run_id.in_(runs)).order_by(LLMRequest.created_at):
        calls.append({k:getattr(row,k) for k in ['id','request_type','provider','model','status','error_code','input_tokens','output_tokens','latency_ms','created_at']})
    encoders=[]
    for row in db.query(WorkflowArtifact).filter(WorkflowArtifact.run_id.in_(runs),WorkflowArtifact.phase.in_(['AGENT_RETRIEVAL_USAGE','MATCHING_AGENTS','AGENT_ENCODER_USAGE'])):
        data=row.payload.get('runtime',{}) if row.phase=='MATCHING_AGENTS' else row.payload
        encoders.append({'run_id':row.run_id,'artifact_id':row.id,'phase':row.phase,'usage':data})
    print(json.dumps({'requests':calls,'encoders':encoders},default=str))
'''.replace('RUN_IDS', repr(runs))
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'backend', 'python', '-'],
                            input=code, text=True, capture_output=True, check=True, encoding='utf8')
    raw = json.loads(result.stdout)
    batches = {}
    for artifact in raw['encoders']:
        if artifact['phase'] != 'AGENT_ENCODER_USAGE':
            continue
        receipt = artifact['usage']
        prior = batches.get(receipt['batch_id'])
        if prior is None or receipt['event'] != 'INTENT':
            batches[receipt['batch_id']] = receipt
    encoder_groups = defaultdict(lambda: {'batch_intents': 0, 'known_batch_attempts': 0,
                                         'successful_texts': 0, 'known_input_tokens': 0,
                                         'unknown_batches': 0, 'unknown_token_attempts': 0,
                                         'external_requests': 0})
    for receipt in batches.values():
        group = encoder_groups[receipt['position']]
        group['batch_intents'] += 1
        group['known_batch_attempts'] += receipt.get('batch_attempts', 0)
        if receipt.get('input_tokens') is None:
            group['unknown_batches'] += 1
            group['unknown_token_attempts'] += receipt.get('batch_attempts', 1)
        else:
            group['known_input_tokens'] += receipt['input_tokens']
            group['successful_texts'] += receipt['texts']
            group['unknown_token_attempts'] += max(0, receipt.get('batch_attempts', 1) - 1)
    groups = defaultdict(lambda: {'requests': 0, 'successes': 0, 'input_tokens': 0,
                                 'output_tokens_including_thinking': 0, 'unknown_usage_requests': 0,
                                 'known_estimated_api_cost_usd': Decimal(0)})
    for row in raw['requests']:
        group = groups[row['request_type']]
        group['requests'] += 1
        group['successes'] += row['status'] == 'SUCCESS'
        if row['input_tokens'] is None or row['output_tokens'] is None:
            group['unknown_usage_requests'] += 1
            continue
        group['input_tokens'] += row['input_tokens']
        group['output_tokens_including_thinking'] += row['output_tokens']
        if row['provider'] == 'gemini' and row['model'] == 'gemini-3.5-flash-lite':
            group['known_estimated_api_cost_usd'] += (
                Decimal(row['input_tokens']) * Decimal('0.30') +
                Decimal(row['output_tokens']) * Decimal('2.50')) / Decimal(1000000)
        else:
            group['unknown_usage_requests'] += 1
    report = {'run_ids': runs, 'billing_basis': 'Measured provider tokens multiplied by published Standard paid-tier list prices; not an invoice',
              'price_date': '2026-10-15', 'price_source': 'https://ai.google.dev/gemini-api/docs/pricing',
              'gemini_3_5_flash_lite_usd_per_million': {'input': '0.30', 'output_including_thinking': '2.50'},
              'llm_by_purpose': dict(groups), 'request_details': raw['requests'],
              'encoder_by_position': dict(encoder_groups), 'encoder_artifacts': raw['encoders'],
              'local_encoder_api_cost_usd': '0',
              'hosting_cpu_electricity_cost': 'Not measured; excluded from API cost'}
    report['known_estimated_total_api_cost_usd'] = str(sum(
        (g['known_estimated_api_cost_usd'] for g in groups.values()), Decimal(0)))
    (OUT / 'usage-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf8')
    print(json.dumps({k: v for k, v in report.items() if k not in ['request_details','encoder_artifacts']}, default=str))


if __name__ == '__main__':
    main()
