"""QC flag bitmask — frozen contract (v1).

Observations are NEVER deleted by QC: each independent check sets one bit.
``qc_flags == 0`` means clean. The consumer chooses rigor via mask.
Bits 6–15 are reserved for future checks; changing existing values is a
breaking change and requires a new contract version.
"""

RANGE = 1
STEP = 2
PERSISTENCE = 4
SPATIAL = 8
METADATA = 16
DUPLICATE = 32

ALL_BITS: dict[str, int] = {
    "RANGE": RANGE,
    "STEP": STEP,
    "PERSISTENCE": PERSISTENCE,
    "SPATIAL": SPATIAL,
    "METADATA": METADATA,
    "DUPLICATE": DUPLICATE,
}


def describe(flags: int) -> list[str]:
    """Human-readable names of the bits set in ``flags``."""
    if flags < 0:
        raise ValueError(f"qc_flags must be non-negative, got {flags}")
    return [name for name, bit in ALL_BITS.items() if flags & bit]


def is_clean(flags: int, mask: int = sum(ALL_BITS.values())) -> bool:
    """True when no bit selected by ``mask`` is set. Default mask: all checks."""
    return (flags & mask) == 0
