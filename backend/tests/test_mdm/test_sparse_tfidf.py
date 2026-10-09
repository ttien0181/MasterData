"""Global recall, record IDF, bounded tiles, deterministic record Top-K."""

from collections import Counter
from dataclasses import replace

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine, CandidateWorkloadExceededError
from masterone.mdm.pipeline.candidate_planner import WorkloadPlanError, WorkloadPolicy, plan_workload
from masterone.mdm.pipeline.stage_runner import StageRunner
from masterone.mdm.pipeline.tfidf_blocking import GlobalTfidfIndex, SparseTfidfConfig, fold_text
from scipy.sparse import csc_matrix, csr_matrix
from sklearn.feature_extraction.text import TfidfVectorizer


def record(rid, name="", **fields):
    return {"id": rid, "normalized_fields": {"name": name, **fields}}


def sparse_config(**kwargs):
    return CandidateConfig(sparse_tfidf=True, include_grams=False, composites=(), **kwargs)


def test_vietnamese_typo_missing_wrong_dob_and_cross_partition():
    records = [record("a", "Nguyễn Văn An", dob="1990-01-01", address_parts={"province": "HN"}),
               record("b", "Nguyen Van An", dob="1980-01-01", address_parts={"province": "HCM"}),
               record("c", "nguyen van anh", dob=""), record("d", "Đặng Thị Hoa")]
    engine = CandidateEngine(sparse_config())
    engine.add_records(records[:1])
    engine.add_records(records[1:])
    result = engine.finalize()
    for pair in (("a", "b"), ("a", "c"), ("b", "c")):
        assert "tfidf_name" in result.pairs[pair]
    assert fold_text("ĐẶNG") == "dang"


def test_full_corpus_idf_preserves_duplicate_and_empty_record_semantics():
    records = [record("a", "nguyen van an"), record("b", "nguyen van an"),
               record("c", "tran thi hoa"), record("d")]
    index = GlobalTfidfIndex(records)
    oracle = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), sublinear_tf=True)
    oracle.fit([fold_text(r["normalized_fields"]["name"]) for r in records])
    for gram, pos in index.vectorizer.vocabulary_.items():
        assert index.idf[pos] == pytest.approx(oracle.idf_[oracle.vocabulary_[gram]])
    assert index.matrix.shape[0] == 2
    assert index.metrics["corpus_records"] == 4


@pytest.mark.parametrize("batch,target", [(1, 1), (3, 2), (64, 256)])
def test_record_topk_matches_exhaustive_sparse_oracle(batch, target):
    records = [record(f"r{i:02}", name) for i, name in enumerate(
        ["nguyen van an"] * 15 + ["nguyen van anh", "nguyen van binh", "tran thi hoa", "nguyen van an"])]
    config = SparseTfidfConfig(top_k=3, query_batch_size=batch, target_batch_size=target)
    index = GlobalTfidfIndex(list(reversed(records)), config=config)
    oracle = TfidfVectorizer(analyzer="char_wb", ngram_range=(3, 4), sublinear_tf=True)
    full = oracle.fit_transform([fold_text(r["normalized_fields"]["name"]) for r in records])
    expected = []
    for i, r in enumerate(records):
        scores = (full[i] @ full.T).tocsr()
        neighbors = [(records[j]["id"], min(1.0, float(score))) for j, score in zip(scores.indices, scores.data)
                     if j != i and score >= config.min_similarity]
        expected.extend((r["id"], rid, score) for rid, score in sorted(neighbors, key=lambda x: (-x[1], x[0]))[:3])
    actual = list(index.search())
    assert {(a, b) for a, b, _ in actual} == {(a, b) for a, b, _ in expected}
    assert max(Counter(a for a, _, _ in actual).values()) <= 3
    assert index.metrics["max_similarity_tile_nnz"] <= batch * target
    assert {(a, b): s for a, b, s in actual} == pytest.approx({(a, b): s for a, b, s in expected})


def test_no_dense_materialization(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("dense materialization")
    monkeypatch.setattr(csr_matrix, "toarray", forbidden)
    monkeypatch.setattr(csc_matrix, "toarray", forbidden)
    records = [record(str(i), "Nguyễn Văn An" if i % 2 else "nguyen van anh") for i in range(100)]
    assert CandidateEngine(sparse_config()).generate(records).pairs


def test_address_sources_and_stage_chunk_equivalence(tmp_path):
    import json
    records = [record("a", "Nguyễn Văn An", address="12 Đường Láng"),
               record("b", "nguyen van anh", address="12 duong lang")]
    config = sparse_config(tfidf=SparseTfidfConfig(fields=("name", "address")))
    small = CandidateEngine(config).generate(records)
    assert small.pairs == {("a", "b"): {"tfidf_name", "tfidf_address"}}
    source = tmp_path / "snapshot.parquet"
    pq.write_table(pa.Table.from_pylist([{"id": r["id"], "normalized_fields": json.dumps(r["normalized_fields"])}
                                        for r in records]), source)
    runner = StageRunner("sparse", "a1", output_dir=tmp_path)
    try:
        assert runner.build_candidates(str(source), str(tmp_path / "pairs.parquet"), batch_size=1, config=config) == small
    finally:
        runner.con.close()


def test_planner_warning_approval_and_snapshot_bound_rejection():
    policy = WorkloadPolicy(warning_pairs=2, approval_pairs=4, hard_pairs=8)
    assert plan_workload({"estimated_pairs": 2}, policy, snapshot_digest="one")["action"] == "ALLOW_WITH_WARNING"
    with pytest.raises(WorkloadPlanError) as error:
        plan_workload({"estimated_pairs": 4}, policy, snapshot_digest="one")
    plan_id = error.value.plan["plan_id"]
    assert plan_workload({"estimated_pairs": 4}, policy, snapshot_digest="one", approved_plan_id=plan_id)
    with pytest.raises(WorkloadPlanError):
        plan_workload({"estimated_pairs": 4}, policy, snapshot_digest="changed", approved_plan_id=plan_id)
    with pytest.raises(WorkloadPlanError) as error:
        plan_workload({"estimated_pairs": 8}, policy, snapshot_digest="one", approved_plan_id=plan_id)
    assert error.value.plan["action"] == "REJECT"


def test_engine_approval_and_memory_gate_before_retrieval(monkeypatch):
    records = [record("a", "abc"), record("b", "abc")]
    cfg = sparse_config(workload=WorkloadPolicy(warning_pairs=1, approval_pairs=2, hard_pairs=100))
    with pytest.raises(WorkloadPlanError) as error:
        CandidateEngine(cfg).generate(records)
    assert CandidateEngine(replace(cfg, approved_plan_id=error.value.plan["plan_id"])).generate(records).pairs
    def forbidden(*args, **kwargs):
        raise AssertionError("index must not be built")
    monkeypatch.setattr("masterone.mdm.pipeline.candidate_engine.GlobalTfidfIndex", forbidden)
    with pytest.raises(WorkloadPlanError) as error:
        CandidateEngine(sparse_config(workload=WorkloadPolicy(memory_budget_bytes=1))).generate(records)
    assert error.value.plan["reasons"] == ["estimated_memory_bytes"]


def test_dot_product_limit_rejects_and_empty_short_corpus():
    with pytest.raises(WorkloadPlanError) as error:
        GlobalTfidfIndex([record("a", "nguyen van an"), record("b", "nguyen van anh")],
                         policy=WorkloadPolicy(max_dot_products=1))
    assert "estimated_dot_products" in error.value.plan["reasons"]
    assert list(GlobalTfidfIndex([]).search()) == []
    assert list(GlobalTfidfIndex([record("a", "x")]).search()) == []


def test_similarity_cell_limit_before_vectorization(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("vectorizer must not run")
    monkeypatch.setattr("sklearn.feature_extraction.text.CountVectorizer.fit_transform", forbidden)
    with pytest.raises(WorkloadPlanError) as error:
        GlobalTfidfIndex([record("a", "abc"), record("b", "abd")],
                         policy=WorkloadPolicy(max_similarity_cells=1))
    assert error.value.plan["reasons"] == ["estimated_similarity_cells"]


def test_sparse_union_actual_pair_limit_and_chunk_determinism():
    records = [record("a", "nguyen van an"), record("b", "nguyen van anh"),
               record("c", "nguyen van ang")]
    cfg = sparse_config(max_unique_pairs=1)
    with pytest.raises(CandidateWorkloadExceededError):
        CandidateEngine(cfg).generate(records)
    config = sparse_config()
    expected = CandidateEngine(config).generate(records)
    engine = CandidateEngine(config)
    for r in reversed(records):
        engine.add_records([r])
    assert engine.finalize() == expected


@pytest.mark.parametrize("kwargs", [{"top_k": 0}, {"fields": ("name", "name")},
                                     {"min_similarity": 0}, {"ngram_range": (4, 3)}])
def test_invalid_config(kwargs):
    with pytest.raises(ValueError):
        SparseTfidfConfig(**kwargs)
