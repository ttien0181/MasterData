import pyarrow.parquet as pq

from masterone.mdm.decision_progress import ProgressScoringEngine, candidate_progress, track_decision
from masterone.mdm.pipeline.scoring_engine import ScoringEngine, MemorySnapshot, ScoringConfig
from tests.test_mdm.test_scoring_engine import full_snapshot, candidate


def test_progress_preserves_score_evidence_and_model_identity(tmp_path):
    snapshot = full_snapshot()
    config = ScoringConfig(batch_size=1)
    baseline = ScoringEngine(MemorySnapshot(snapshot), config)
    measured = ProgressScoringEngine(MemorySnapshot(snapshot), config)
    pairs = [candidate('a', 'b'), candidate('a', 'z0')]
    updates = []
    baseline.write_scores(iter(pairs), tmp_path / 'baseline.parquet', expected_count=2)
    with track_decision(lambda *args: updates.append(args)):
        candidate_progress(0, force=True)
        candidate_progress(2, force=True)
        measured.write_scores(iter(pairs), tmp_path / 'measured.parquet', expected_count=2)
    assert pq.read_table(tmp_path / 'baseline.parquet').equals(pq.read_table(tmp_path / 'measured.parquet'))
    assert baseline.identity == measured.identity
    assert updates[:2] == [('candidate-generation', 0, 0), ('candidate-generation', 2, 0)]
    assert updates[2] == ('pair-classification', 0, 2)
    assert updates[-1] == ('pair-classification', 2, 2)
    candidate_progress(5, force=True)
    assert updates[-1] == ('pair-classification', 2, 2)  # no cross-attempt callback leak
