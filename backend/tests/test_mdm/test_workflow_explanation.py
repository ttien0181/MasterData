from masterone.config import get_settings
from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_workflow_contract import request
from tests.test_mdm.test_workflow_stages import read, ready_decisions


def test_pair_explanation_sends_aggregate_signals_and_keeps_name_variants(client, workflow_run, monkeypatch):
    ready_decisions(client, workflow_run)
    url=f'/api/v1/mdm/runs/{workflow_run}/workflow'
    with client.session_factory.begin() as db:
        pair=db.query(WorkflowPair).filter_by(run_id=workflow_run).first()
        pair_id=pair.id
        pair.assessment={**pair.baseline, 'field_evidence':[
            {'field':'name','state':'NA','left':'Sensitive Name','right':'Private Name'},
            {'field':'phone','state':'EX','left':'0901234567','right':'0901234567'},
            {'field':'email','state':'MI'},
        ]}
    captured=[]
    def explain(payload, settings, **kwargs):
        captured.append(payload)
        return {'status':'READY','explanation':{'summary_vi':'Tên là biến thể tương đương.','limitations':[]}}
    monkeypatch.setattr(get_settings(),'llm_enabled',True)
    monkeypatch.setattr(get_settings(),'llm_explanation_enabled',True)
    monkeypatch.setattr('masterone.mdm.workflow_explanation.explain_cohort',explain)
    response=client.post(url+f'/pairs/{pair_id}/explanation',json=request(read(client,workflow_run)['workflow_revision']))
    assert response.status_code==200
    assert response.json()['status']=='READY'
    assert captured[0]['field_signal_counts']=={'name':{'similar':1},'phone':{'exact':1},'email':{'missing':1}}
    assert set(captured[0])=={'pair_count','unique_record_count','field_signal_counts','group','subgroup','score_is_probability'}
    assert captured[0]['score_is_probability'] is False


def test_pair_explanation_discards_result_if_pair_changes_during_provider_call(client, workflow_run, monkeypatch):
    ready_decisions(client,workflow_run)
    url=f'/api/v1/mdm/runs/{workflow_run}/workflow'
    pair_id=client.get(url+'/pairs').json()['items'][0]['pair_id']
    def explain(payload,settings,**kwargs):
        with client.session_factory.begin() as db:
            db.get(WorkflowPair,pair_id).revision+=1
        return {'status':'READY','explanation':{'summary_vi':'Old result','limitations':[]}}
    monkeypatch.setattr(get_settings(),'llm_enabled',True)
    monkeypatch.setattr(get_settings(),'llm_explanation_enabled',True)
    monkeypatch.setattr('masterone.mdm.workflow_explanation.explain_cohort',explain)
    response=client.post(url+f'/pairs/{pair_id}/explanation',json=request(read(client,workflow_run)['workflow_revision']))
    assert response.status_code==200
    assert response.json()['status']=='STALE'
    assert response.json()['explanation'] is None
