"""Snapshot-wide blocking with global identity, guards and complete provenance.

Preserves P-119 blocking policy, with opt-in global sparse TF-IDF. Index and
candidate union remain in memory; workload estimates are not an OS RSS limit.
"""

import json
import logging
import pickle
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from hashlib import sha256
from itertools import combinations

from masterone.mdm.pipeline.candidate_planner import WorkloadPolicy, plan_workload
from masterone.mdm.pipeline.person_blocking import (
    PERSON_RECOVERY_BLOCK_SIZE,
    PERSON_RECOVERY_SOURCES,
    person_recovery_keys,
)
from masterone.mdm.pipeline.tfidf_blocking import (
    GlobalTfidfIndex, SparseTfidfConfig, _top_k_pairs, fold_text, tfidf_candidates,
)


def canonical_pair(left: str, right: str) -> tuple[str, str] | None:
    return tuple(sorted((left, right))) if left != right else None


def _ngrams(value: str, size: int = 3) -> set[str]:
    padded = f"  {value} "
    return {padded[i:i + size] for i in range(max(0, len(padded) - size + 1))}


@dataclass(frozen=True)
class CompositeKey:
    field: str
    transform: str = "identity"
    length: int = 3
    require_valid: bool = False

    def __post_init__(self):
        if self.transform not in {"identity", "prefix", "suffix", "year", "street"}:
            raise ValueError("Unknown composite transformation")
        if type(self.length) is not int or self.length < 1:
            raise ValueError("Composite length must be positive")

    def value(self, fields: dict) -> str:
        value = fields
        for part in self.field.split("."):
            value = value.get(part, "") if isinstance(value, dict) else ""
        if not isinstance(value, str) or not value:
            return ""
        if self.require_valid and fields.get("_field_quality", {}).get(self.field) != "VALID":
            return ""
        if self.transform == "street":
            from masterone.mdm.pipeline.review_recovery import address_key
            return address_key(value)
        if self.transform in {"prefix", "suffix"}:
            if len(value) < self.length:
                return ""
            return value[:self.length] if self.transform == "prefix" else value[-self.length:]
        if self.transform == "year":
            return value[:4] if len(value) >= 4 else ""
        return value


@dataclass(frozen=True)
class CompositeRule:
    source: str
    keys: tuple[CompositeKey, ...]
    max_block_size: int = 30

    def __post_init__(self):
        if not self.source or not self.keys or type(self.max_block_size) is not int or self.max_block_size < 1:
            raise ValueError("Composite rule needs source, keys and positive block size")


DEFAULT_COMPOSITES = (
    CompositeRule("name_prefix_year", (CompositeKey("name", "prefix"), CompositeKey("dob", "year"))),
    CompositeRule("surname_year", (CompositeKey("_name_parts.surname"), CompositeKey("dob", "year"))),
    CompositeRule("name_province", (CompositeKey("name"), CompositeKey("address_parts.province")), 40),
    CompositeRule("phone_suffix_year", (CompositeKey("phone", "suffix", 4, True), CompositeKey("dob", "year"))),
    CompositeRule("name_full_dob", (CompositeKey("name", require_valid=True), CompositeKey("dob", require_valid=True))),
    CompositeRule("dob_numbered_street", (CompositeKey("dob", require_valid=True),
                                          CompositeKey("address", "street", require_valid=True)), 20),
)

_MVP_WEAK_SOURCES = frozenset({"exact_name", "name_gram", "address_gram", "name_prefix_year",
                               "surname_year", "name_province", "phone_suffix_year"})


def _conditional_source(source):
    return source in _MVP_WEAK_SOURCES or source.startswith("tfidf_")


def _corroboration_keys(fields, contact_frequencies):
    """Cheap secondary partitions, used only for candidate review, never merge permission.

    Full DOB, month/day plus two tokens, or numbered street plus a token retain
    plausible review routes. Shared/historical contacts cannot corroborate weak
    name/address text. Tokens are capped to bound pathological input fanout.
    """
    from masterone.mdm.pipeline.matching import SHARED_CONTACT_MIN_FREQ
    from masterone.mdm.pipeline.review_recovery import address_key
    from masterone.mdm.pipeline.standardization import parse_dob

    keys = set()
    quality = fields.get("_field_quality", {})
    for field_name, collection in (("phone", "phones"), ("email", "emails")):
        risks = (f"_{field_name}_shared", f"_{field_name}_shared_risk",
                 f"{field_name}_shared", f"{field_name}_shared_risk")
        if any(str(fields.get(key, '')).lower() in {'true', '1'} for key in risks):
            continue
        items = fields.get(collection, [])
        if len(items) > 32:
            continue
        for item in items:
            value = item.get("value")
            if (value and item.get("quality") == "VALID"
                    and not any(item.get(key) for key in ('shared', 'shared_risk', 'historical', 'valid_to', 'origin_id'))
                    and contact_frequencies.get((field_name, value), 0) <= SHARED_CONTACT_MIN_FREQ):
                keys.add((field_name, value))
    gov_id = fields.get("government_id")
    if gov_id and quality.get("government_id") == "VALID":
        keys.add(('government_id', fields.get('government_id_namespace', ''),
                  fields.get('government_id_type', ''), gov_id))
    tokens = sorted({token for token in fold_text(fields.get('name')).split()
                     if len(token) >= 2 and token.isalpha()})
    if len(tokens) > 12:
        tokens = []
    if quality.get('dob') == 'VALID':
        dob = parse_dob(fields.get('dob') or '')
        if dob['quality'] == 'VALID' and dob['month'] and dob['day']:
            keys.add(('full_dob', dob['canonical']))
            for a, b in combinations(tokens, 2):
                keys.add(('month_day_tokens', dob['month'], dob['day'], a, b))
    street = address_key(fields.get('address'))
    if street and quality.get('address') not in {'INVALID', 'PLACEHOLDER', 'MISSING'}:
        keys.update(('street_token', street, token) for token in tokens)
    return keys


def _mvp_tfidf_vectors(snapshot, text_field, pools, config, policy):
    """Compressed global IDF with workload estimates for actual supported searches.

    GlobalTfidfIndex plans a global search at construction, so using that class
    here would incorrectly reject a large corpus with tiny supported pools.
    """
    import numpy as np
    from sklearn.feature_extraction.text import CountVectorizer
    from sklearn.preprocessing import normalize

    postings = defaultdict(list)
    for record in snapshot:
        text = fold_text(record['normalized_fields'].get(text_field))
        if text:
            postings[text].append(record['id'])
    documents = sorted(postings)
    document_rows = {text: row for row, text in enumerate(documents)}
    record_rows = {rid: document_rows[text] for text, ids in postings.items() for rid in ids}
    query_rows = [[record_rows[rid] for rid in ids if rid in record_rows] for ids in pools]
    query_rows = [rows for rows in query_rows if len(rows) >= 2]
    gram_bound = sum(len(text) for text in documents) * (config.ngram_range[1] - config.ngram_range[0] + 1)
    largest_pool = max(map(len, query_rows), default=0)
    details = {
        'source': f'tfidf_{text_field}', 'record_count': len(snapshot), 'unique_vectors': len(documents),
        'estimated_similarity_cells': sum(len(rows) ** 2 for rows in query_rows),
        'estimated_pairs': sum(len(rows) * min(config.top_k, len(rows) - 1) for rows in query_rows),
        'estimated_memory_bytes': gram_bound * 512 + len(snapshot) * 256 + largest_pool ** 2 * 24,
    }
    digest = sha256(json.dumps([(r['id'], fold_text(r['normalized_fields'].get(text_field)))
                                for r in snapshot]).encode()).hexdigest()
    plan_workload(details, policy, snapshot_digest=digest)
    matrix = None
    if documents:
        vectorizer = CountVectorizer(analyzer='char_wb', ngram_range=config.ngram_range,
                                     lowercase=False, dtype=np.float64)
        try:
            counts = vectorizer.fit_transform(documents).tocsr()
        except ValueError as error:
            if 'empty vocabulary' not in str(error):
                raise
        else:
            present = counts.copy()
            present.data[:] = 1
            frequencies = np.asarray(present.T @ np.array([len(postings[text]) for text in documents])).ravel()
            idf = np.log((1 + len(snapshot)) / (1 + frequencies)) + 1
            counts.data = 1 + np.log(counts.data)
            matrix = normalize(counts.multiply(idf), norm='l2').tocsr()
            # Count actual pool-local shared-gram products before retrieval.
            dot_products = 0
            for rows in query_rows:
                frequencies = defaultdict(int)
                for column in matrix[rows].indices:
                    frequencies[int(column)] += 1
                dot_products += sum(n ** 2 for n in frequencies.values())
            details['estimated_dot_products'] = dot_products
            plan_workload(details, policy, snapshot_digest=digest)
    return matrix, record_rows, details


def _recall_v2_enabled():
    from masterone.mdm.engine_adapter import feature
    return feature('candidate_recall_v2')


@dataclass(frozen=True)
class CandidateConfig:
    max_block_size: int = 60
    name_block_size: int = 60
    contact_block_size: int = 20
    gram_block_size: int = 15
    max_pairs_per_block: int = 1770  # C(60, 2), retains all legacy default blocks
    max_unique_pairs: int | None = 50_000_000
    composites: tuple[CompositeRule, ...] = DEFAULT_COMPOSITES
    include_grams: bool = True
    include_tfidf: bool = True
    sparse_tfidf: bool = False  # opt-in until production recall/RSS validation
    tfidf: SparseTfidfConfig = field(default_factory=SparseTfidfConfig)
    workload: WorkloadPolicy = field(default_factory=WorkloadPolicy)
    approved_plan_id: str | None = None
    recall_v2: bool = field(default_factory=_recall_v2_enabled)
    recall_v2_pair_budget: int = 200_000
    mvp_step2: bool = False  # opt-in conditional review candidates; legacy defaults stay unchanged

    def __post_init__(self):
        if type(self.mvp_step2) is not bool:
            raise ValueError('MVP step 2 flag must be boolean')
        if type(self.recall_v2) is not bool or type(self.recall_v2_pair_budget) is not int or self.recall_v2_pair_budget < 1:
            raise ValueError('Recall v2 needs a boolean flag and a positive pair budget')
        for value in (self.max_block_size, self.name_block_size, self.contact_block_size, self.gram_block_size, self.max_pairs_per_block):
            if type(value) is not int or value < 1:
                raise ValueError("Candidate limits must be positive integers")
        if self.max_unique_pairs is not None and (type(self.max_unique_pairs) is not int or self.max_unique_pairs < 1):
            raise ValueError("Global pair limit must be positive or None")
        sources = [rule.source for rule in self.composites]
        reserved = {"exact_phone", "exact_email", "exact_gov_id", "exact_name", "name_gram", "address_gram", "tfidf_name", "tfidf_address"}
        reserved.update(PERSON_RECOVERY_SOURCES)
        if len(set(sources)) != len(sources) or reserved.intersection(sources):
            raise ValueError("Composite sources must be distinct from every other pass")


class _PairSources(Mapping):
    """Read-only view: canonical pair -> set of generating sources, decoded from bitmasks on access.

    Iterates in sorted pair order, like the dict it replaces, so consumers see the same sequence.
    """

    def __init__(self, masks: dict, bits: dict[str, int]):
        self._masks = masks
        self._bits = bits
        self._keys = sorted(masks)

    def __getitem__(self, pair):
        mask = self._masks[pair]
        return {name for name, bit in self._bits.items() if mask & bit}

    def __iter__(self):
        return iter(self._keys)

    def __len__(self):
        return len(self._keys)


@dataclass
class CandidateResult:
    pairs: dict[tuple[str, str], set[str]]
    metrics: dict
    block_log: list[dict] = field(default_factory=list)


class CandidateWorkloadExceededError(RuntimeError):
    def __init__(self, metrics: dict):
        super().__init__("Global candidate pair limit exceeded; partial candidates must not be published")
        self.metrics = metrics


def _isolated_copy(value):
    """Independent copy of JSON-like data. Same isolation as deepcopy, several times faster."""
    return pickle.loads(pickle.dumps(value, pickle.HIGHEST_PROTOCOL))


class CandidateEngine:
    """Ingest any number of chunks into one snapshot index before finalization.

    IDs are global snapshot IDs, never chunk offsets or source external IDs.
    Duplicate IDs fail explicitly rather than silently overwriting identity.
    """

    def __init__(self, config: CandidateConfig | None = None):
        self.config = config or CandidateConfig()
        self.records: dict[str, dict] = {}
        self.blocks: dict[tuple[str, tuple[str, ...]], set[str]] = defaultdict(set)
        self.excluded: dict[str, int] = defaultdict(int)

    def _retained(self, fields: dict) -> dict:
        """Independent copy of the fields later passes read (TF-IDF text and partitions).

        Blocking keys are derived here, at ingest, so the rest of the record is not kept.
        The sparse TF-IDF path digests whole records, so it keeps everything.
        """
        if self.config.sparse_tfidf:
            return _isolated_copy(fields)
        kept = {key: fields[key] for key in ("name", "address", "dob") if key in fields}
        if self.config.mvp_step2:
            for key in ('phones', 'emails', 'government_id', 'government_id_namespace',
                        'government_id_type', '_field_quality', '_phone_shared', '_phone_shared_risk',
                        'phone_shared', 'phone_shared_risk', '_email_shared', '_email_shared_risk',
                        'email_shared', 'email_shared_risk'):
                if key in fields:
                    kept[key] = fields[key]
        address_parts = fields.get("address_parts")
        if isinstance(address_parts, dict) and "province" in address_parts:
            kept["address_parts"] = {"province": address_parts["province"]}
        return _isolated_copy(kept)

    def add_records(self, records):
        for record in records:
            record_id = record["id"]
            if not isinstance(record_id, str) or not record_id:
                raise ValueError("Snapshot record ID must be a nonempty string")
            if record_id in self.records:
                raise ValueError("Duplicate snapshot record ID")
            fields = record["normalized_fields"]
            self.records[record_id] = {"id": record_id, "normalized_fields": self._retained(fields)}
            if self.config.mvp_step2:
                raw = record.get('raw_fields', {})
                retained = self.records[record_id]['normalized_fields']
                for field_name in ('phone', 'email'):
                    if any(str(raw.get(key, '')).strip().lower() in {'true', '1'} for key in
                           (f'_{field_name}_shared', f'_{field_name}_shared_risk',
                            f'{field_name}_shared', f'{field_name}_shared_risk')):
                        retained[f'_{field_name}_shared_risk'] = True
            for field_name, collection in (("phone", "phones"), ("email", "emails")):
                source = f"exact_{field_name}"
                values = {item["value"] for item in fields.get(collection, [])
                          if item.get("value") and item.get("quality") == "VALID"}
                if not values:
                    self.excluded[source] += 1
                for value in values:
                    self.blocks[(source, (value,))].add(record_id)
            gov_id = fields.get("government_id", "")
            if gov_id and fields.get("_field_quality", {}).get("government_id", "MISSING") == "VALID":
                self.blocks[("exact_gov_id", (gov_id,))].add(record_id)
            else:
                self.excluded["exact_gov_id"] += 1
            name = fields.get("name", "")
            if name:
                self.blocks[("exact_name", (name,))].add(record_id)
            else:
                self.excluded["exact_name"] += 1
            if self.config.include_grams:
                for source, value in (("name_gram", name), ("address_gram", fields.get("address", ""))):
                    if not value:
                        self.excluded[source] += 1
                    for gram in _ngrams(value) if value else ():
                        self.blocks[(source, (gram,))].add(record_id)
            for rule in self.config.composites:
                key = tuple(part.value(fields) for part in rule.keys)
                if all(key):
                    self.blocks[(rule.source, key)].add(record_id)
                else:
                    self.excluded[rule.source] += 1
            if self.config.recall_v2:
                keys = list(person_recovery_keys(record))
                if not keys:
                    for source in PERSON_RECOVERY_SOURCES:
                        self.excluded[source] += 1
                for source, key in keys:
                    self.blocks[(source, key)].add(record_id)

    def finalize(self) -> CandidateResult:
        config = self.config
        limits = {rule.source: rule.max_block_size for rule in config.composites}
        limits.update({source: config.contact_block_size for source in ("exact_phone", "exact_email", "exact_gov_id")})
        limits.update(exact_name=config.name_block_size, name_gram=config.gram_block_size, address_gram=config.gram_block_size)
        limits.update({source: PERSON_RECOVERY_BLOCK_SIZE for source in PERSON_RECOVERY_SOURCES})
        pair_budget = config.max_unique_pairs
        if config.recall_v2:
            pair_budget = min(pair_budget, config.recall_v2_pair_budget) if pair_budget is not None else config.recall_v2_pair_budget
        # canonical pair -> bitmask of the sources that generated it; sets per pair cost several times more memory.
        pairs: dict[tuple[str, str], int] = {}
        source_bits: dict[str, int] = {}
        metrics = {"record_count": len(self.records), "candidate_count": 0,
                   "generated_observations": 0, "skipped_pair_observations": 0,
                   "duplicate_observations": 0, "skip_reasons": {}, "sources": {}}
        metrics["disabled_passes"] = ([] if config.include_grams else ["name_gram", "address_gram"]) + (
            [] if config.include_tfidf else [f"tfidf_{name}" for name in (
                config.tfidf.fields if config.sparse_tfidf else ("name",))])
        block_log = []
        corroboration = {}
        if config.mvp_step2:
            frequencies = {(source.removeprefix('exact_'), key[0]): len(ids)
                           for (source, key), ids in self.blocks.items()
                           if source in {'exact_phone', 'exact_email'}}
            corroboration = {rid: _corroboration_keys(record['normalized_fields'], frequencies)
                             for rid, record in self.records.items()}
            metrics['conditional_candidates'] = {
                'enabled': True, 'version': 'mvp_step2_v1', 'rejected_pair_observations': 0,
                'unique_pairs_after_gate': 0,
                'rejection_count_unit': 'source/block observations, not distinct pairs',
            }

        def reject(source, count):
            stats = metrics['sources'].setdefault(source, {'generated_pairs': 0, 'skipped_pairs': 0})
            stats['conditional_rejected_observations'] = stats.get('conditional_rejected_observations', 0) + count
            stats['skipped_pairs'] += count
            metrics['conditional_candidates']['rejected_pair_observations'] += count
            metrics['skipped_pair_observations'] += count
            metrics['skip_reasons']['secondary_corroboration_missing'] = (
                metrics['skip_reasons'].get('secondary_corroboration_missing', 0) + count)

        if config.sparse_tfidf and not config.mvp_step2:
            estimated_pairs = sum(len(ids) * (len(ids) - 1) // 2
                for (source, _), ids in self.blocks.items()
                if len(ids) <= min(config.max_block_size, limits[source])
                and len(ids) * (len(ids) - 1) // 2 <= config.max_pairs_per_block)
            index_memory = 0
            if config.include_tfidf:
                for text_field in config.tfidf.fields:
                    texts = {fold_text(r["normalized_fields"].get(text_field)) for r in self.records.values()}
                    valid = sum(bool(fold_text(r["normalized_fields"].get(text_field))) for r in self.records.values())
                    estimated_pairs += valid * min(config.tfidf.top_k, max(0, len(self.records) - 1))
                    grams = sum(len(text) for text in texts) * (config.tfidf.ngram_range[1] - config.tfidf.ngram_range[0] + 1)
                    index_memory = max(index_memory, grams * 512 + len(self.records) * 256
                        + config.tfidf.query_batch_size * config.tfidf.target_batch_size * 24
                        + config.tfidf.query_batch_size * (config.tfidf.top_k + 1) * 128)
            signature = {"records": [self.records[key] for key in sorted(self.records)],
                         "config": {k: v for k, v in asdict(config).items() if k != "approved_plan_id"}}
            digest = sha256(json.dumps(signature, sort_keys=True).encode()).hexdigest()
            metrics["workload_plan"] = plan_workload({"record_count": len(self.records),
                "max_block_size": max(map(len, self.blocks.values()), default=0),
                "oversized_blocks": sum(len(ids) > min(config.max_block_size, limits[source])
                    for (source, _), ids in self.blocks.items()),
                "estimated_pairs": estimated_pairs,
                "estimated_memory_bytes": index_memory + estimated_pairs * 512
                    + sum(map(len, self.blocks.values())) * 128}, config.workload,
                snapshot_digest=digest, approved_plan_id=config.approved_plan_id)

        def merge(left, right, sources):
            pair = canonical_pair(left, right)
            if pair is None:
                return
            for source in sorted(sources):
                if (config.mvp_step2 and _conditional_source(source)
                        and not corroboration[left].intersection(corroboration[right])):
                    reject(source, 1)
                    continue
                stats = metrics["sources"].setdefault(source, {"generated_pairs": 0, "skipped_pairs": 0})
                stats["generated_pairs"] += 1
                metrics["generated_observations"] += 1
                bit = source_bits.setdefault(source, 1 << len(source_bits))
                mask = pairs.get(pair, 0)
                if mask & bit:
                    metrics["duplicate_observations"] += 1
                pairs[pair] = mask | bit
            metrics["candidate_count"] = len(pairs)
            if pair_budget is not None and len(pairs) > pair_budget:
                metrics["failure_reason"] = "global_pair_limit_exceeded"
                logging.getLogger(__name__).error("candidate_workload %s", json.dumps(metrics, sort_keys=True))
                raise CandidateWorkloadExceededError(metrics)

        for (source, key), ids in sorted(self.blocks.items()):
            size = len(ids)
            potential = size * (size - 1) // 2
            reason = ("max_block_size" if size > min(config.max_block_size, limits[source]) else
                      "max_pairs_per_block" if potential > config.max_pairs_per_block else
                      "singleton" if size < 2 else "eligible")
            skipped = potential if reason not in {"eligible", "singleton"} else 0
            block_log.append({"source": source, "block_key_sha256": sha256(json.dumps(key).encode()).hexdigest(),
                              "block_size": size, "potential_pairs": potential,
                              "generated_pairs": potential - skipped, "skipped_pairs": skipped, "reason": reason})
            stats = metrics["sources"].setdefault(source, {"generated_pairs": 0, "skipped_pairs": 0})
            stats["skipped_pairs"] += skipped
            metrics["skipped_pair_observations"] += skipped
            if skipped:
                metrics["skip_reasons"][reason] = metrics["skip_reasons"].get(reason, 0) + skipped
            elif potential:
                emissions = combinations(sorted(ids), 2)
                if config.mvp_step2 and _conditional_source(source):
                    # Partition before quadratic expansion. Deduplicate only the passing
                    # pairs within this bounded block, never materialize rejected pairs.
                    partitions = defaultdict(list)
                    for rid in sorted(ids):
                        for support_key in corroboration[rid]:
                            partitions[support_key].append(rid)
                    emissions = sorted({pair for members in partitions.values()
                                        for pair in combinations(members, 2)})
                    rejected = potential - len(emissions)
                    reject(source, rejected)
                    block_log[-1]['conditional_rejected_observations'] = rejected
                    block_log[-1]['generated_pairs'] = len(emissions)
                    block_log[-1]['skipped_pairs'] += rejected
                for left, right in emissions:
                    merge(left, right, {source})
        metrics["excluded_records_by_source"] = dict(sorted(self.excluded.items()))
        metrics["exclusion_reason"] = "missing_value_or_required_valid_quality"
        if config.include_tfidf:
            # The legacy helper silently returns no vectors without sklearn.
            # Require the declared dependency so coverage loss is explicit.
            from sklearn.feature_extraction.text import TfidfVectorizer  # noqa: F401

            snapshot = [self.records[key] for key in sorted(self.records)]
            if config.mvp_step2:
                # Fit global corpus IDF once per field, then retrieve within
                # corroborated pools. Gating after global top-k lets unsupported
                # lookalikes crowd out supported neighbors before the gate sees them.
                partitions = defaultdict(list)
                for rid in sorted(corroboration):
                    for support_key in corroboration[rid]:
                        partitions[support_key].append(rid)
                pools = sorted({tuple(ids) for ids in partitions.values() if len(ids) >= 2})
                eligible_pools = []
                oversized = 0
                rejected_observations = 0
                for ids in pools:
                    potential = len(ids) * (len(ids) - 1) // 2
                    if len(ids) > config.max_block_size or potential > config.max_pairs_per_block:
                        oversized += 1
                        rejected_observations += potential
                    else:
                        eligible_pools.append(ids)
                metrics['conditional_candidates']['tfidf_partitions'] = {
                    'unique_supported_pools': len(pools), 'searched_pools': len(eligible_pools),
                    'oversized_pools': oversized,
                    'oversized_pool_pair_observations': rejected_observations,
                    'max_pool_size': config.max_block_size,
                    'idf_scope': 'snapshot', 'top_k_scope': 'secondary_corroboration_pool',
                }
                metrics['tfidf'] = {}
                policy = replace(config.workload, warning_pairs=config.workload.hard_pairs,
                                 approval_pairs=config.workload.hard_pairs)
                for text_field in config.tfidf.fields if config.sparse_tfidf else ('name',):
                    source = f'tfidf_{text_field}'
                    stats = metrics['sources'].setdefault(source, {'generated_pairs': 0, 'skipped_pairs': 0})
                    stats['skipped_pairs'] += rejected_observations
                    metrics['skipped_pair_observations'] += rejected_observations
                    if rejected_observations:
                        reason = 'tfidf_secondary_partition_limit'
                        metrics['skip_reasons'][reason] = metrics['skip_reasons'].get(reason, 0) + rejected_observations
                    if not eligible_pools:
                        metrics['tfidf'][text_field] = {'status': 'no_eligible_corroborated_pool'}
                        continue
                    matrix, record_rows, workload = _mvp_tfidf_vectors(
                        snapshot, text_field, eligible_pools, config.tfidf, policy)
                    observations = 0
                    if matrix is not None:
                        for ids in eligible_pools:
                            usable = [(rid, record_rows.get(rid)) for rid in ids]
                            usable = [(rid, row) for rid, row in usable if row is not None]
                            if len(usable) < 2:
                                continue
                            ids, rows = zip(*usable)
                            candidates = _top_k_pairs(list(ids), matrix[list(rows)],
                                                      config.tfidf.top_k, config.tfidf.min_similarity)
                            observations += len(candidates)
                            for left, right in sorted(candidates):
                                merge(left, right, {source})
                    metrics['tfidf'][text_field] = {
                        **workload, 'status': 'searched_corroborated_pools',
                        'corpus_records': len(snapshot), 'pool_pair_observations': observations,
                        'matrix_nnz': matrix.nnz if matrix is not None else 0,
                    }
                    del matrix
            elif config.sparse_tfidf:
                metrics["tfidf"] = {}
                # Engine approval covers the aggregate plan; each index still
                # enforces hard memory, operation and emission limits.
                policy = replace(config.workload, warning_pairs=config.workload.hard_pairs,
                                 approval_pairs=config.workload.hard_pairs)
                for text_field in config.tfidf.fields:
                    index = GlobalTfidfIndex(snapshot, field=text_field, config=config.tfidf, policy=policy)
                    for left, right, _score in index.search():
                        merge(left, right, {f"tfidf_{text_field}"})
                    metrics["tfidf"][text_field] = index.metrics
                    del index
            else:
                for (left, right), sources in sorted(tfidf_candidates(snapshot).items()):
                    merge(left, right, sources)
        if config.recall_v2:
            metrics['recall_v2'] = {'enabled': True, 'version': 'person_name_dob_v1',
                'pair_budget': pair_budget, 'max_block_size': PERSON_RECOVERY_BLOCK_SIZE,
                'oversized_blocks': sum(log['source'] in PERSON_RECOVERY_SOURCES and
                    log['reason'] in {'max_block_size', 'max_pairs_per_block'} for log in block_log),
                'warning': 'CANDIDATE_PAIR_WARNING' if len(pairs) >= pair_budget * .8 else None}
        metrics["union_collapsed_observations"] = metrics["generated_observations"] - len(pairs)
        if config.mvp_step2:
            metrics['conditional_candidates']['unique_pairs_after_gate'] = len(pairs)
        logging.getLogger(__name__).info("candidate_workload %s", json.dumps(metrics, sort_keys=True))
        return CandidateResult(_PairSources(pairs, source_bits), metrics, block_log)

    def generate(self, records) -> CandidateResult:
        self.add_records(records)
        return self.finalize()
