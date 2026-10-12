"""Ephemeral auth remains in process memory and is revoked after browser checks."""
import json
import os
import subprocess
import argparse

CREATE = '''import hashlib,json,secrets,time
from masterone.auth.models import AuthSession
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.mdm.models import ResolutionRun
e,f=create_database(get_settings().active_mdm_database_url)
with f.begin() as db:
 r=db.get(ResolutionRun,'3449137c-8f4d-41cc-9152-fa12768aa355')
 token=secrets.token_urlsafe(32)
 s=AuthSession(user_id=r.owner_user_id,token_hash=hashlib.sha256(token.encode()).hexdigest(),expires_at_epoch=int(time.time())+1200)
 db.add(s);db.flush();print(json.dumps({'token':token,'session_id':s.id,'run_id':r.id}))
e.dispose()
'''

def main(script='verify-pair-assessment.mjs'):
    result=subprocess.run(['docker','exec','-i','p119-session-qa-backend-1','python','-'],input=CREATE,
                          capture_output=True,text=True,check=True)
    auth=json.loads(result.stdout)
    try:
        subprocess.run(['node','frontend/scripts/'+script],check=True,
            env={**os.environ,'PAIR_ASSESSMENT_TOKEN':auth['token'],'PAIR_ASSESSMENT_RUN':auth['run_id']})
    finally:
        cleanup='''from masterone.auth.models import AuthSession
from masterone.config import get_settings
from masterone.db.session import create_database
from masterone.db.base import now_utc
e,f=create_database(get_settings().active_mdm_database_url)
with f.begin() as db:
 s=db.get(AuthSession,%r)
 if s:s.revoked_at=now_utc()
e.dispose()
''' % auth['session_id']
        subprocess.run(['docker','exec','-i','p119-session-qa-backend-1','python','-'],input=cleanup,
                       capture_output=True,text=True,check=True)

if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--script',choices=['verify-pair-assessment.mjs','verify-review-queue.mjs'],default='verify-pair-assessment.mjs')
    main(parser.parse_args().script)
