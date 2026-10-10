from uuid import UUID

from masterone.mdm.ids import new_ordered_uuid


def test_ordered_uuids_are_valid_v7_unique_and_strictly_increasing():
    ids = [new_ordered_uuid() for _ in range(50_000)]
    assert len(set(ids)) == len(ids)
    assert ids == sorted(ids)
    assert all(UUID(value).version == 7 for value in ids[:1000])
    assert all(len(value) == 36 for value in ids)
