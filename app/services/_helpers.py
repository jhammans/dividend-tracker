# app/services/_helpers.py
import pandas as pd
import numpy as np

def to_native(df: pd.DataFrame) -> pd.DataFrame:
    """
    Convert all pandas/numpy types to Python-native types.
    Ensures datetimes are tz-naive and Python datetime instances.
    """
    df = df.copy()

    for col in df.columns:
        if pd.api.types.is_datetime64_any_dtype(df[col]):
            # Convert tz-aware → UTC → naive
            df[col] = (
                df[col]
                .dt.tz_convert("UTC", nonexistent="shift_forward", ambiguous="NaT")
                .dt.tz_localize(None)
            )
        else:
            # Convert values independently
            df[col] = df[col].apply(_to_native_scalar)

    return df


def _to_native_scalar(v):
    if isinstance(v, (np.generic,)):
        return v.item()
    if isinstance(v, pd.Timestamp):
        if v.tzinfo is not None:
            # Convert tz-aware to naive UTC
            return v.tz_convert("UTC").tz_localize(None).to_pydatetime()
        return v.to_pydatetime()
    return v
