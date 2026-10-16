def test_projected_token_keeps_existing_preview_contract(client, workflow_run):
    from hashlib import sha256
    from masterone.mdm.generation_staging import digest
    from masterone.mdm.models import ResolutionNegativeEdge, ResolutionRun
    from masterone.mdm.workflow_bulk import selected_pairs
    from masterone.mdm.workflow_pair_reads import bulk_preview_token
    from tests.test_mdm.test_pair_review_redesign import seed_pairs

    seed_pairs(client, workflow_run)
    payload = {'decision': 'merge', 'scope': {'filter': {'group': 'A'}}, 'auto_only': False}
    with client.session_factory() as db:
        run = db.get(ResolutionRun, workflow_run)
        # Frozen token contract: evidence/revisions remain covered after reducing
        # the SQL projection; an already-open confirmation must stay valid.
        legacy = sha256(digest(payload).encode())
        for row in selected_pairs(db, run.id, payload['scope']).yield_per(1000):
            legacy.update(digest({'id': row.id, 'revision': row.revision, 'decision': row.decision,
                                  'evaluation': row.assessment or row.baseline}).encode())
        for left, right, identifier in db.query(ResolutionNegativeEdge.left_record_id,
                ResolutionNegativeEdge.right_record_id, ResolutionNegativeEdge.id).filter_by(
                run_id=run.id, active=True).order_by(ResolutionNegativeEdge.id):
            legacy.update(digest([left, right, identifier]).encode())
        assert bulk_preview_token(db, run, payload) == legacy.hexdigest()
