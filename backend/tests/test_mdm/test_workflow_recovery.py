"""Recovered candidates stay manual and publish only after explicit decisions."""
import pytest

from masterone.config import get_settings
from tests.test_mdm.test_workflow_stages import ready_decisions,read,run_one


def create(client,monkeypatch,conflicting_name=False):
    monkeypatch.setattr(get_settings(),'session_workflow_enabled',True)
    monkeypatch.setattr(get_settings(),'pair_assessment_auto_enabled',True)
    second='Tran Thi Binh' if conflicting_name else 'Nguyen Van An'
    address='123 Nguyen Trai, Thanh Xuan, Ha Noi' if conflicting_name else ''
    csv=f'source_record_id,source_system,entity_type,name,phone,email,dob,address\n1,CRM,PERSON,Nguyen Van An,,,1990-05-15,"{address}"\n2,BILL,PERSON,{second},,,1990-05-15,"{address}"\n'
    response=client.post('/api/v1/mdm/runs',data={'workflow_version':'2'},files={'files':('recovery.csv',csv.encode(),'text/csv')})
    assert response.status_code==201,response.text
    rid=response.json()['run_id'];ready_decisions(client,rid)
    root=f'/api/v1/mdm/runs/{rid}/workflow'
    pair=client.get(root+'/pairs?resolution=REQUIRED').json()['items'][0]
    assert pair['baseline']['assessment_version']=='person-review-assessment-v4'
    assert pair['baseline']['rule_conclusion']=='INSUFFICIENT_EVIDENCE'
    assert pair['eligibility']['manual_allowed'] and not pair['eligibility']['auto_allowed']
    return rid,root,pair


def body(client,rid,pid,decision,reason=None):
    w=read(client,rid)
    data=dict(request_id=pid+'-'+decision,expected_workflow_revision=w['workflow_revision'],
        expected_assessment_id=w['assessment_id'],expected_pair_revision=0,decision=decision)
    if reason:data['reason']=reason
    return data


@pytest.mark.parametrize('conflicting_name',[False,True])
def test_recovered_pair_can_publish_after_explicit_acceptance(client,monkeypatch,conflicting_name):
    rid,root,p=create(client,monkeypatch,conflicting_name)
    assert client.get(root+'/pairs/summary').json()['needs_review']==1
    if conflicting_name:
        blocked=client.post(root+f"/pairs/{p['pair_id']}/decision",json=body(client,rid,p['pair_id'],'merge'))
        assert blocked.status_code==409 and 'SOFT_ACCEPT_REASON_REQUIRED' in blocked.text
    accepted=client.post(root+f"/pairs/{p['pair_id']}/decision",json=body(client,rid,p['pair_id'],'merge','Source documents confirm identity' if conflicting_name else None))
    assert accepted.status_code==200,accepted.text
    w=read(client,rid)
    final=client.post(root+'/finalize-decisions',json=dict(request_id='publish-recovered',
        expected_workflow_revision=w['workflow_revision'],expected_assessment_id=w['assessment_id'],
        expected_decision_revision=w['decision_revision']))
    assert final.status_code==202,final.text
    run_one(client)
    assert read(client,rid)['workflow_state']=='GOLDEN_REVIEW'
    groups=client.get(root+'/clusters').json()
    assert groups['total']==1 and groups['items'][0]['member_count']==2


def test_recovered_pair_rejection_remains_separate_without_deleting_evidence(client,monkeypatch):
    rid,root,p=create(client,monkeypatch)
    response=client.post(root+f"/pairs/{p['pair_id']}/decision",json=body(client,rid,p['pair_id'],'no_merge'))
    assert response.status_code==200,response.text
    w=read(client,rid)
    final=client.post(root+'/finalize-decisions',json=dict(request_id='publish-separate',expected_workflow_revision=w['workflow_revision'],
        expected_assessment_id=w['assessment_id'],expected_decision_revision=w['decision_revision']))
    assert final.status_code==202,final.text
    run_one(client)
    assert client.get(root+'/clusters').json()['total']==2
    retained=client.get(root+'/pairs').json()['items'][0]
    assert retained['decision']=='REJECTED' and retained['baseline']['review_support']
