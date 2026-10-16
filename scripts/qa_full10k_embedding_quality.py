"""Post-hoc, read-only embedding diagnostics on completed full10k QA runs.

Cosine is not a probability. Report ranking and coverage without selecting a
threshold on this test set. No truth is passed to the encoder or matching API.
"""
import argparse
import json
from pathlib import Path

from qa_api_multiagent import docker_python, save
from qa_full10k import fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset',type=Path,required=True)
    parser.add_argument('--case-output',type=Path,required=True)
    parser.add_argument('--service',choices=('backend','worker'),default='backend')
    args=parser.parse_args()
    out=args.case_output.resolve()
    run=json.loads((out/'report.json').read_text(encoding='utf8'))['run_id']
    _,_,truth,_,external=fixture(args.dataset.resolve(),out)
    entity_by_external={source+'\x1f'+rid:truth[key]['entity_id'] for (source,rid),key in external.items()}
    code='''
import json
from collections import Counter,defaultdict
from sqlalchemy import create_engine,select,text
from sqlalchemy.orm import Session
from sklearn.metrics import roc_auc_score
from masterone.config import get_settings
from masterone.mdm.models import ResolutionRecord
from masterone.mdm.workflow_models import WorkflowArtifact
from masterone.mdm.workflow_artifacts import iter_list
gold=GOLD
with Session(create_engine(get_settings().active_mdm_database_url)) as db:
    db.execute(text('SET TRANSACTION READ ONLY'))
    records=db.execute(select(ResolutionRecord.id,ResolutionRecord.source_system,ResolutionRecord.external_id).where(ResolutionRecord.run_id==RUN))
    labels={rid:gold[source+'\\x1f'+external] for rid,source,external in records}
    artifact=db.query(WorkflowArtifact).filter_by(run_id=RUN,phase='MATCHING_AGENTS').order_by(WorkflowArtifact.created_at.desc()).first()
    if artifact is None: raise ValueError('NO_MATCHING_AGENTS_ARTIFACT')
    values=defaultdict(list)
    states=defaultdict(Counter)
    for row in iter_list(db,artifact.id,RUN,'MATCHING_AGENTS',['pairs']):
        positive=labels[row['key']['left_id']]==labels[row['key']['right_id']]
        for field,value in (row.get('embedding') or {}).get('fields',{}).items():
            states[field][value['status']]+=1
            if value['status']=='OK': values[field].append((int(positive),value['cosine']))
    report={}
    for field,counts in states.items():
        pairs=values[field]
        ys=[v[0] for v in pairs]
        scores=[v[1] for v in pairs]
        report[field]={'pair_status_counts':dict(counts),'available_pair_count':len(pairs),
            'roc_auc_on_available_candidates':float(roc_auc_score(ys,scores)) if len(set(ys))==2 else None,
            'score_means':{name:sum(v for y,v in pairs if y==label)/sum(y==label for y,v in pairs)
                if any(y==label for y,v in pairs) else None for label,name in [(1,'truth_match'),(0,'truth_non_match')]}}
    print(json.dumps({'fields':report,'record_coverage':artifact.payload.get('runtime',{}).get('field_coverage'),
        'notes':['Post-hoc diagnostic; no test-set threshold tuning.','AUC is conditional on available candidate pairs; does not measure retrieval recall or merge precision.','Evidence profile keeps routing based on rules; embedding alone creates no decisions.']}))
'''.replace('GOLD',repr(entity_by_external)).replace('RUN',repr(run))
    save(out/'embedding-quality.json',docker_python(code,service=args.service))


if __name__=='__main__':
    main()
