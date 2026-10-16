"""Global exact tiled record top-K; dedup vectors never drops record postings."""
import time
from collections import defaultdict

import numpy as np

from masterone.mdm.generation_staging import digest

from .contracts import eligible_text


class RetrievalBudget:
    def __init__(self, config):
        self.config = config['execution_budget']
        self.started = time.monotonic()
        self.cells = 0

    def reserve(self, cells=0, workspace=0):
        self.cells += cells
        if (self.cells > self.config['similarity_cells'] or workspace > self.config['workspace_bytes']
                or time.monotonic() - self.started > self.config['run_seconds']):
            raise ValueError('RETRIEVAL_BUDGET_EXCEEDED')


def retrieve_vectors(records: list[dict], field_vectors: dict, config: dict, progress, *, budget=None) -> dict:
    meter = budget or RetrievalBudget(config)
    ids = [r['id'] for r in records]
    if any(not isinstance(rid, str) or not rid for rid in ids) or len(set(ids)) != len(ids):
        raise ValueError('RETRIEVAL_SNAPSHOT_ID_INVALID')
    if set(field_vectors) != {'name', 'address'}:
        raise ValueError('RETRIEVAL_FIELD_COVERAGE')
    cfg = config['retrieval']
    k, threshold = cfg['embedding']['top_k'], cfg['embedding']['min_similarity']
    dimension = config['encoder_identity']['dimension']
    observations, metrics = [], {}
    for field, text_field in (('name', 'name_unicode'), ('address', 'address')):
        entry = field_vectors[field]
        if (entry['encoder_digest'] != config['encoder_identity']['bundle_digest']
                or entry['preprocessing_digest'] != digest(config['preprocessing'])):
            raise ValueError('RETRIEVAL_VECTOR_IDENTITY_MISMATCH')
        eligible = {r['id'] for r in records if eligible_text(r['normalized_fields'].get(text_field),
                    r['normalized_fields'].get('_field_quality', {}).get(field, 'MISSING'))[1] is None}
        vectors = entry['vectors']
        if set(vectors) != eligible:
            raise ValueError('RETRIEVAL_VECTOR_COVERAGE_INCOMPLETE')
        postings, values = defaultdict(list), {}
        # Reserve conservative working memory before array allocation; vectors supplied by caller count in process-tree RSS.
        meter.reserve(workspace=len(vectors) * (dimension * 12 + 512)
                      + cfg['query_batch_size'] * cfg['target_batch_size'] * 8)
        for rid in sorted(vectors):
            vector = np.asarray(vectors[rid], dtype=np.float64)
            if vector.shape != (dimension,) or not np.isfinite(vector).all():
                raise ValueError('RETRIEVAL_VECTOR_INVALID')
            scale = np.max(np.abs(vector))
            if not scale:
                raise ValueError('RETRIEVAL_ZERO_VECTOR')
            vector = vector / scale
            normalized = (vector / np.linalg.norm(vector)).astype(np.float32)
            key = normalized.tobytes()
            postings[key].append(rid)
            values[key] = normalized
        keys = sorted(postings, key=lambda key: postings[key][0])
        size = len(keys)
        meter.reserve(cells=size * size)
        if size:
            matrix = np.stack([values[key] for key in keys])
            for start in range(0, size, cfg['query_batch_size']):
                end = min(size, start + cfg['query_batch_size'])
                best = [[] for _ in range(end - start)]
                for offset in range(0, size, cfg['target_batch_size']):
                    meter.reserve()
                    stop = min(size, offset + cfg['target_batch_size'])
                    tile = matrix[start:end] @ matrix[offset:stop].T
                    for local, scores in enumerate(tile):
                        candidates = best[local] + [(min(1., float(score)), offset + pos)
                                                   for pos, score in enumerate(scores) if score >= threshold]
                        best[local] = sorted(candidates, key=lambda item: (-item[0], postings[keys[item[1]]][0]))[:k + 1]
                    progress('embedding_retrieval_' + field, offset + (end - start) * size, size * size)
                for local, neighbors in enumerate(best):
                    for query_id in postings[keys[start + local]]:
                        ranked = sorted(((score, target_id) for score, pos in neighbors
                                         for target_id in postings[keys[pos]][:k + 1] if target_id != query_id),
                                        key=lambda item: (-item[0], item[1]))[:k]
                        for rank, (score, target_id) in enumerate(ranked, 1):
                            observations.append({'pass': 'embedding_' + field, 'field': field,
                                'query_id': query_id, 'target_id': target_id, 'rank': rank, 'similarity': score,
                                'left_id': min(query_id, target_id), 'right_id': max(query_id, target_id),
                                'encoder_digest': entry['encoder_digest'],
                                'preprocessing_digest': entry['preprocessing_digest']})
        metrics[field] = {'eligible_records': len(eligible), 'masked_records': len(records) - len(eligible),
                          'unique_vectors': size, 'queried_records': len(eligible),
                          'eligible_success_ratio': 1. if eligible else None}
    return {'observations': sorted(observations, key=lambda o: (o['pass'], o['query_id'], o['rank'])),
            'fields': metrics, 'similarity_cells': meter.cells, 'status': 'COMPLETE'}
