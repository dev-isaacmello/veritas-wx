"""Contract validation: loud, specific failures. Never silently coerce."""

import polars as pl


class ContractError(ValueError):
    """A DataFrame does not satisfy its declared contract."""


def validate(
    df: pl.DataFrame,
    schema: dict[str, pl.DataType],
    name: str,
    allow_extra: bool = False,
) -> pl.DataFrame:
    """Validate ``df`` against a frozen contract schema.

    Checks column presence and exact dtypes. Returns ``df`` unchanged so calls
    can be chained at stage boundaries. Raises ContractError listing EVERY
    problem found (not just the first) — debugging a pipeline one error at a
    time is how silent data loss hides.
    """
    problems: list[str] = []

    missing = [c for c in schema if c not in df.columns]
    if missing:
        problems.append(f"missing columns: {missing}")

    if not allow_extra:
        extra = [c for c in df.columns if c not in schema]
        if extra:
            problems.append(f"unexpected columns: {extra}")

    for col, expected in schema.items():
        if col in df.columns:
            actual = df.schema[col]
            if actual != expected:
                problems.append(f"column '{col}': expected {expected}, got {actual}")

    if problems:
        raise ContractError(f"contract violation [{name}]: " + "; ".join(problems))
    return df


def require_non_null(df: pl.DataFrame, columns: list[str], name: str) -> pl.DataFrame:
    """Fail loudly when contract-critical columns contain nulls."""
    counts = {c: df[c].null_count() for c in columns if c in df.columns}
    bad = {c: n for c, n in counts.items() if n > 0}
    if bad:
        raise ContractError(f"contract violation [{name}]: null values in {bad}")
    return df
