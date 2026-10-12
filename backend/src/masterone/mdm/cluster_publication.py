from hashlib import sha256

from masterone.mdm.pipeline.golden import build_golden


def with_singletons(records, proposals, run_id, revision):
    by_id = {r['id']: r for r in records}
    covered = []
    clusters = []
    for proposal in proposals:
        members = proposal['member_record_ids']
        covered.extend(members)
        clusters.append({**proposal, 'id': sha256(f'{run_id}:{revision}:{proposal["id"]}'.encode()).hexdigest(), 'singleton': False})
    if len(covered) != len(set(covered)) or not set(covered) <= by_id.keys():
        raise ValueError('WORKFLOW_MEMBERSHIP_INVALID')
    for rid in sorted(by_id.keys() - set(covered)):
        clusters.append({'id': sha256(f'{run_id}:{revision}:singleton:{rid}'.encode()).hexdigest(),
            'member_record_ids': [rid], 'golden_fields': build_golden([rid], by_id), 'singleton': True, 'status': 'SINGLETON'})
    if sum(len(c['member_record_ids']) for c in clusters) != len(records):
        raise ValueError('WORKFLOW_MEMBERSHIP_COVERAGE')
    return clusters
