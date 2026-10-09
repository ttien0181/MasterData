"""Compatibility entry point for snapshot-wide candidate generation."""

from masterone.mdm.pipeline.candidate_engine import CandidateConfig, CandidateEngine
from masterone.mdm.pipeline.candidate_engine import _ngrams as _ngrams


def generate_candidates(records: list[dict], max_bucket_size: int = 60,
                        top_k: int = 12) -> dict[tuple[str, str], set[str]]:
    """Keep the legacy signature; top_k was and remains unused by rule passes."""
    del top_k
    return CandidateEngine(CandidateConfig(
        max_block_size=max(60, max_bucket_size), name_block_size=max_bucket_size,
        max_pairs_per_block=max(1770, max_bucket_size * (max_bucket_size - 1) // 2),
    )).generate(records).pairs
