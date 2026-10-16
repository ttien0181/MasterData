from masterone.mdm.workflow_models import WorkflowPair
from tests.test_mdm.test_pair_review_redesign import seed_pairs


def test_actions_taxonomy_filter_summary_and_dto_preserve_matching_groups(client,workflow_run):
    url=seed_pairs(client,workflow_run)
    with client.session_factory.begin() as db:
        row=db.query(WorkflowPair).filter_by(run_id=workflow_run,id='pair-0000').one()
        row.baseline={**row.baseline,'primary_group':'D','subgroup':'D_MULTI','decision':'REVIEW'}
    summary=client.get(url+'/pairs/summary?visibility=review&taxonomy=actions_v1').json()
    assert next(g for g in summary['groups'] if g['key']=='B')['total']==1
    page=client.get(url+'/pairs?visibility=review&taxonomy=actions_v1&group=B&subgroup=B_SOFT_CONFLICT').json()
    assert page['total']==1
    pair=page['items'][0]
    assert pair['review_group']=='B'
    assert pair['review_subgroup']=='B_SOFT_CONFLICT'
    assert pair['baseline']['primary_group']=='D'
    assert client.get(url+'/pairs?group=D').json()['total']==1
