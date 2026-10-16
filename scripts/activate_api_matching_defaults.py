"""Select measured API evidence defaults for existing local QA profile owners.

Existing runs/profiles remain immutable. This is technical activation for review,
not a production quality certificate. No model API requests are made here.
"""
import argparse
import json
from pathlib import Path

from qa_api_multiagent import ROOT, docker_python, save


def validate_activation_proof(proof, current_digest):
    if proof.get('status') != 'PASS' or len(proof.get('runs', [])) < 2:
        raise ValueError('INCOMPLETE_ACTIVATION_PROOF')
    if not all(r['variant'] == 'evidence' and r['status'] == 'PASS'
               and r['machine_quality_before_truth_replay']['candidate_recall'] == 1.
               and r['final_quality_after_scripted_truth_review']['records_preserved'] == r['fixture']['record_count']
               for r in proof['runs']):
        raise ValueError('FAILED_ACTIVATION_PROOF')
    digests = {r.get('profile_digest') for r in proof['runs']}
    if len(digests) != 1 or None in digests:
        raise ValueError('INCONSISTENT_MEASURED_PROFILES')
    expected = digests.pop()
    if expected != current_digest:
        raise ValueError('MEASURED_PROFILE_RUNTIME_MISMATCH')
    return expected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--evidence', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    proof = json.loads(args.evidence.read_text(encoding='utf8'))
    target = args.output.resolve()
    assert target.is_relative_to((ROOT / 'artifacts/api-multiagent-20261015').resolve()) and not target.exists()
    current = docker_python('''
import json
from masterone.mdm.generation_staging import digest
from masterone.mdm.matching_foundation_service import baseline_config
from masterone.mdm.matching_agents.embedding_runtime import advanced_config
print(json.dumps({'digest':digest(advanced_config(baseline_config(),'fail_operation'))}))
''')
    expected = validate_activation_proof(proof, current['digest'])
    result = docker_python('''
import json
from sqlalchemy import create_engine,func
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from fastapi import HTTPException
from masterone.config import get_settings
from masterone.auth.models import User
from masterone.auth.security import ROLE_CAPABILITIES
from masterone.mdm.matching_foundation_models import MatchingProfile,MatchingProfileDefault
from masterone.mdm.matching_foundation_contracts import ProfileCreate,DefaultApply
from masterone.mdm.matching_foundation_service import create_profile,transition_profile,apply_default,verify_profile
from masterone.mdm.matching_foundation_service import baseline_config
from masterone.mdm.matching_agents.embedding_runtime import advanced_config
from masterone.mdm.generation_staging import digest
s=get_settings()
assert s.app_env=='test' and make_url(s.active_mdm_database_url).host=='database'
assert s.embedding_provider=='openrouter' and s.advanced_matching_agents_enabled
expected=MEASURED_PROFILE_DIGEST
if digest(advanced_config(baseline_config(),'fail_operation'))!=expected:
    raise RuntimeError('MEASURED_PROFILE_RUNTIME_MISMATCH')
factory=sessionmaker(create_engine(s.active_mdm_database_url),expire_on_commit=False)
with factory() as db:
    owners=[row.owner_user_id for row in db.query(MatchingProfileDefault).filter_by(data_scope='owner-private')]
result=[]
for owner in owners:
    with factory.begin() as db:
        actor=db.get(User,owner)
        if not actor or not actor.is_active or not {'learn_match_profile','apply_match_profile'} <= ROLE_CAPABILITIES.get(actor.role,frozenset()):
            continue
        pointer=db.get(MatchingProfileDefault,(owner,'owner-private'))
        previous=db.get(MatchingProfile,pointer.profile_id)
        if previous.config_json.get('kind')=='RULE_WITH_EMBEDDING_EVIDENCE' and previous.config_json.get('encoder_identity',{}).get('provider')=='openrouter':
            try:
                verify_profile(previous,current=True)
                if previous.config_digest!=expected:
                    raise RuntimeError('MEASURED_PROFILE_RUNTIME_MISMATCH')
                result.append({'owner_id':owner,'status':'ALREADY_CURRENT','profile_id':previous.id})
                continue
            except HTTPException:
                pass
        version=(db.query(func.max(MatchingProfile.version)).filter_by(owner_user_id=owner,data_scope='owner-private').scalar() or 0)+1
        profile=create_profile(db,owner,ProfileCreate(version=version,advanced_agents=True,fallback_mode='fail_operation'))
        if profile.config_digest!=expected:
            raise RuntimeError('MEASURED_PROFILE_RUNTIME_MISMATCH')
        transition_profile(db,owner,profile.id,1,'VALIDATED')
        apply_default(db,owner,'owner-private',DefaultApply(profile_id=profile.id,expected_revision=pointer.revision))
        result.append({'owner_id':owner,'status':'UPDATED_FOR_NEW_RUNS_ONLY','previous_profile_id':previous.id,
                       'profile_id':profile.id,'profile_digest':profile.config_digest,'version':version})
print(json.dumps({'scope':'EXISTING_LOCAL_QA_OWNERS_ONLY','quality_certified':False,'measured_profile_digest':expected,'defaults':result}))
'''.replace('MEASURED_PROFILE_DIGEST', repr(expected)))
    save(target, result)
    print(json.dumps({'owners': len(result['defaults']), 'quality_certified': False,
                      'updated': sum(r['status'] == 'UPDATED_FOR_NEW_RUNS_ONLY' for r in result['defaults'])}))


if __name__ == '__main__':
    main()
