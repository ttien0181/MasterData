"""Small record type shared by the FEBRL loaders and local benchmarks."""

from dataclasses import dataclass
from typing import Any


@dataclass
class RawRecord:
    system: str
    source_id: str
    given_name: str | None = None
    surname: str | None = None
    date_of_birth: str | None = None
    postcode: str | None = None
    state: str | None = None
    suburb: str | None = None
    address: str | None = None
    address_1: str | None = None
    address_2: str | None = None
    phone: str | None = None

    def __setstate__(self, state: dict[str, Any]) -> None:
        # Old caches contain Pydantic RawRecord objects from pipeline.models.
        self.__dict__.update(state.get("__dict__", state))
