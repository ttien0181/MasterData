"""Activate validated hybrid defaults for active QA Stewards; retain old profiles and runs."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/four-ai-20261015'


def main():
    live = json.loads((OUT / 'live-run.json').read_text(encoding='utf8'))
    assert live.get('status') == 'COMPLETED', 'Complete real four-position validation first'
    code = '''
import json
from sqlalchemy import create_engine,func
from sqlalchemy.engine import make_url
from fastapi import HTTPException
from sqlalchemy.orm import sessionmaker
from masterone.config import get_settings
from masterone.auth.models import User
from masterone.auth.security import ROLE_CAPABILITIES
from masterone.mdm.matching_foundation_models import MatchingProfile,MatchingProfileDefault
from masterone.mdm.matching_foundation_contracts import ProfileCreate,DefaultApply
from masterone.mdm.matching_foundation_service import create_profile,transition_profile,apply_default,verify_profile
s=get_settings()
assert s.app_env!='production' and s.advanced_matching_integration_enabled
assert make_url(s.active_mdm_database_url).host=='database', 'Expected the existing local QA database service'
factory=sessionmaker(create_engine(s.active_mdm_database_url),expire_on_commit=False)
result=[]
with factory() as db:
    actors=[u.id for u in db.query(User).filter_by(is_active=True) if {'learn_match_profile','apply_match_profile'}<=ROLE_CAPABILITIES.get(u.role,frozenset())]
for actor in actors:
    with factory.begin() as db:
        pointer=db.get(MatchingProfileDefault,(actor,'owner-private'))
        prior=db.get(MatchingProfile,pointer.profile_id) if pointer else None
        if prior and prior.config_json.get('kind')=='RULE_HYBRID_DETERMINISTIC':
            try:
                verify_profile(prior,current=True)
                result.append({'owner_id':actor,'status':'ALREADY_HYBRID','profile_id':prior.id});continue
            except HTTPException:
                pass
        version=(db.query(func.max(MatchingProfile.version)).filter_by(owner_user_id=actor).scalar() or 0)+1
        previous={'profile_id':pointer.profile_id,'revision':pointer.revision} if pointer else None
        profile=create_profile(db,actor,ProfileCreate(version=version,integration=True,fallback_mode='fail_operation'))
        transition_profile(db,actor,profile.id,1,'VALIDATED')
        default=apply_default(db,actor,'owner-private',DefaultApply(profile_id=profile.id,expected_revision=pointer.revision if pointer else 0))
        result.append({'owner_id':actor,'status':'ACTIVATED','profile_id':profile.id,'default_revision':default.revision,'previous_default':previous})
print(json.dumps({'scope':'Active QA Stewards, new runs only; existing bindings preserved','owners':result}))
'''
    result = subprocess.run(['docker', 'compose', 'exec', '-T', 'backend', 'python', '-'],
                            input=code, text=True, capture_output=True, check=True, encoding='utf8')
    report = json.loads(result.stdout)
    (OUT / 'activated-defaults.json').write_text(json.dumps(report, indent=2), encoding='utf8')
    print(json.dumps({'active_stewards': len(report['owners']),
                      'changed_defaults': sum(o['status'] == 'ACTIVATED' for o in report['owners'])}))


if __name__ == '__main__':
    main()
