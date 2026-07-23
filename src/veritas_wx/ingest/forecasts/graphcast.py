"""GraphCast (AIWP ``GRAP_v100_GFS``) selective HDF5 reads (ADR-0002 §4).

One netCDF4/HDF5 file per run (~5.8 GB) holds ALL leads f000..f240/6h.
Downloading whole files is forbidden (4.2 TB for the window); surface
variables are chunked (1, 721, 1440) — one full grid per timestep, ~2-4 MB
gzip — so per-(var, lead) chunk reads over HTTP ranges transfer only what
Phase 1 needs (~280 MB/run pro-rata vs 5.8 GB).

Field facts verified against the real 2025-08-01 00Z file:
- t2 [K], u10/v10 [m s-1]; apcp is the 6-h bucket in METERS (units attr 'm'
  — extract.py converts by units, never by assumption);
- latitude 90 -> -90, longitude 0 -> 359.75 (same graticule as GFS 0.25);
- time axis: 41 epoch-second stamps, init + 6h steps.

Output plugs into the same ``match.extract`` path as the GRIB models via
DecodedField with GRIB-convention short names (2t/10u/10v/tp).
"""

import datetime as dt

import fsspec
import h5py
import numpy as np

from veritas_wx.match.extract import DecodedField

BUCKET_URL = "https://noaa-oar-mlwp-data.s3.amazonaws.com"
PREFIX = "GRAP_v100_GFS"
STEP_HOURS = 6
N_STEPS = 41  # f000..f240

# HDF5 dataset -> (GRIB-convention shortName, units attr we REQUIRE)
VAR_MAP: dict[str, tuple[str, str]] = {
    "t2": ("2t", "K"),
    "u10": ("10u", "m s-1"),
    "v10": ("10v", "m s-1"),
    "apcp": ("tp", "m"),  # meters! extract.precip converts m -> mm by units
}


def object_url(init: dt.datetime, base: str = BUCKET_URL) -> str:
    stamp = init.strftime("%Y%m%d%H")
    return (
        f"{base}/{PREFIX}/{init:%Y}/{init:%m%d}/"
        f"{PREFIX}_{stamp}_f000_f240_06.nc"
    )


class GraphCastRun:
    """One remote run file, opened lazily; reads only requested chunks.

    Not a context manager by accident: thin_slice holds it across the lead
    loop so coordinate arrays and HDF5 metadata are fetched once per init.
    """

    def __init__(self, init: dt.datetime, base: str = BUCKET_URL, block_size: int = 1 << 22):
        self.init = init
        self.url = object_url(init, base)
        self._fh = fsspec.open(self.url, "rb", block_size=block_size).open()
        self._h5 = h5py.File(self._fh, "r")
        self.lats = self._h5["latitude"][:].astype(np.float64)
        self.lons = self._h5["longitude"][:].astype(np.float64)
        self._times = self._h5["time"][:]
        for name, (_, expected_units) in VAR_MAP.items():
            units = self._h5[name].attrs["units"]
            units = units.decode() if isinstance(units, bytes) else str(units)
            if units != expected_units:
                raise ValueError(
                    f"GraphCast {name}: units {units!r} != expected {expected_units!r} "
                    f"({self.url}) — refusing to guess conversions"
                )

    def close(self) -> None:
        self._h5.close()
        self._fh.close()

    def _step_index(self, lead_hours: int) -> int:
        if lead_hours % STEP_HOURS != 0 or not 0 <= lead_hours <= 240:
            raise ValueError(f"GraphCast lead must be 0..240 step 6, got {lead_hours}")
        idx = lead_hours // STEP_HOURS
        expected = int((self.init + dt.timedelta(hours=lead_hours)).timestamp())
        actual = int(self._times[idx])
        if actual != expected:
            raise ValueError(
                f"GraphCast time axis mismatch at +{lead_hours}h: file says "
                f"{actual}, init implies {expected} ({self.url})"
            )
        return idx

    def fields_at_lead(self, lead_hours: int) -> list[DecodedField]:
        """Read the 4 Phase 1 grids for one lead (4 chunk reads)."""
        idx = self._step_index(lead_hours)
        fields: list[DecodedField] = []
        for name, (short_name, units) in VAR_MAP.items():
            values = self._h5[name][idx].astype(np.float64)
            fields.append(
                DecodedField(
                    short_name=short_name,
                    lats=self.lats,
                    lons=self.lons,
                    values=values,
                    units=units,
                    step=str(lead_hours),
                )
            )
        return fields
