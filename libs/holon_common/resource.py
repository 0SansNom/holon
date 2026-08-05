"""Classification and lineage propagation.

`Classification` and `most_restrictive()` compute every ObjectType's
classification level from its sources.
"""

from __future__ import annotations

from enum import Enum


class Classification(str, Enum):
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"


_CLASSIFICATION_RANK = {
    Classification.PUBLIC: 0,
    Classification.INTERNAL: 1,
    Classification.CONFIDENTIAL: 2,
    Classification.RESTRICTED: 3,
}


def most_restrictive(*values: "Classification | str") -> Classification:
    """A derived resource inherits the most restrictive classification
    of its lineage parents.
    """
    classifications = [Classification(v) for v in values]
    if not classifications:
        return Classification.INTERNAL
    return max(classifications, key=lambda c: _CLASSIFICATION_RANK[c])
