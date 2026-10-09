"""Entity-disjoint split integrity checks; pair labels never define a new split."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

from standalone.contracts.core import LabeledPair


@dataclass(frozen=True)
class SplitIntegrityReport:
    valid: bool
    entity_overlap: Mapping[str, tuple[str, ...]]
    record_overlap: Mapping[str, tuple[str, ...]]
    cross_partition_pairs: tuple[str, ...]
    pair_split_mismatches: tuple[str, ...]
    unassigned_pair_endpoints: tuple[str, ...]
    records_missing_entity_labels: tuple[str, ...]
    missing_required_partitions: tuple[str, ...]


def check_split_integrity(record_partition: Mapping[str, str], entity_by_record: Mapping[str, str],
                          pairs: Sequence[LabeledPair],
                          required_partitions: Sequence[str] = ()) -> SplitIntegrityReport:
    entity_partitions: dict[str, set[str]] = {}
    missing_entities = tuple(sorted(record_id for record_id in record_partition if not entity_by_record.get(record_id)))
    for record_id, partition in record_partition.items():
        entity = entity_by_record.get(record_id)
        if entity is not None:
            entity_partitions.setdefault(entity, set()).add(partition)
    entity_overlap = {entity: tuple(sorted(parts)) for entity, parts in entity_partitions.items() if len(parts) > 1}

    pair_splits: dict[str, set[str]] = {}
    for pair in pairs:
        for record_id in (pair.pair.left_record_id, pair.pair.right_record_id):
            if record_id in record_partition:
                pair_splits.setdefault(record_id, set()).add(pair.split)
    record_overlap = {record_id: tuple(sorted(parts)) for record_id, parts in pair_splits.items() if len(parts) > 1}

    cross_partition: list[str] = []
    mismatches: list[str] = []
    unassigned: list[str] = []
    for labeled in pairs:
        left = record_partition.get(labeled.pair.left_record_id)
        right = record_partition.get(labeled.pair.right_record_id)
        if left is None:
            unassigned.append(labeled.pair.left_record_id)
        if right is None:
            unassigned.append(labeled.pair.right_record_id)
        if left is not None and right is not None and left != right:
            cross_partition.append(labeled.pair.pair_id)
        if left is not None and right is not None and (left != labeled.split or right != labeled.split):
            mismatches.append(labeled.pair.pair_id)
    present = set(record_partition.values()) | {pair.split for pair in pairs}
    missing = tuple(sorted(set(required_partitions) - present))
    return SplitIntegrityReport(
        valid=not (entity_overlap or record_overlap or cross_partition or mismatches or unassigned or missing_entities or missing),
        entity_overlap=entity_overlap,
        record_overlap=record_overlap,
        cross_partition_pairs=tuple(cross_partition),
        pair_split_mismatches=tuple(mismatches),
        unassigned_pair_endpoints=tuple(sorted(set(unassigned))),
        records_missing_entity_labels=missing_entities,
        missing_required_partitions=missing,
    )
