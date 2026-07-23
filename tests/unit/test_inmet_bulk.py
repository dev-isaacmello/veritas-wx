"""Unit tests for the INMET bulk dadoshistoricos parser (ADR-0002 §2).

The fixture reproduces the REAL 2026.zip format quirks: decimal comma with
leading digit omitted (",8"), empty fields AND -9999 both meaning missing,
trailing ';', accent variations across vintages.
"""

import datetime as dt
import zipfile

import pytest

from veritas_wx.ingest.observations.inmet_bulk import parse_station_csv, rows_from_zip
from veritas_wx.runlog import log_stage

HEADER = (
    "Data;Hora UTC;PRECIPITAÇÃO TOTAL, HORÁRIO (mm);RADIACAO GLOBAL (Kj/m²);"
    "TEMPERATURA DO AR - BULBO SECO, HORARIA (°C);VENTO, VELOCIDADE HORARIA (m/s);"
)


def make_csv(native_id: str = "A001", header: str = HEADER, data_lines: list[str] | None = None):
    meta = [
        "REGIAO:;CO",
        "UF:;DF",
        "ESTACAO:;BRASILIA",
        f"CODIGO (WMO):;{native_id}",
        "LATITUDE:;-15,78944444",
        "LONGITUDE:;-47,92583332",
        "ALTITUDE:;1160,96",
        "DATA DE FUNDACAO:;07/05/00",
    ]
    if data_lines is None:
        data_lines = [
            "2026/01/01;0000 UTC;0;;19,9;1,1;",  # all 3 present
            "2026/01/01;0100 UTC;,8;;-9999;;",  # precip ,8=0.8; t2m sentinel; wind empty
            "garbage;;;;;;",  # bad timestamp
        ]
    return "\n".join(meta + [header] + data_lines) + "\n"


class TestParseStationCsv:
    def test_native_id_from_metadata_not_filename(self):
        native_id, _, _, _ = parse_station_csv(make_csv(native_id="A042"), "test-0")
        assert native_id == "A042"

    def test_rows_and_units(self):
        _, df, _, _ = parse_station_csv(make_csv(), "test-0")
        first_hour = df.filter(df["valid_time"] == dt.datetime(2026, 1, 1, tzinfo=dt.UTC))
        by_var = {r["variable"]: r for r in first_hour.to_dicts()}
        assert by_var["precip_1h"]["value"] == 0.0
        assert by_var["t2m"]["value"] == pytest.approx(293.05)  # 19.9 C -> K
        assert by_var["wind10m"]["value"] == pytest.approx(1.1)
        assert by_var["t2m"]["station_id"] == "inmet:A001"
        assert by_var["t2m"]["source"] == "inmet"

    def test_leading_digit_omitted_decimal(self):
        """The real files write 0.8 as ',8' — must parse, not drop."""
        _, df, _, _ = parse_station_csv(make_csv(), "test-0")
        h1 = df.filter(
            (df["valid_time"] == dt.datetime(2026, 1, 1, 1, tzinfo=dt.UTC))
            & (df["variable"] == "precip_1h")
        )
        assert h1["value"][0] == pytest.approx(0.8)

    def test_reconciliation_identity(self):
        """potential = data_lines * 3 == emitted + itemized drops (guard R9)."""
        _, df, dropped, _ = parse_station_csv(make_csv(), "test-0")
        assert dropped == {"bad_timestamp": 3, "value_missing": 2, "value_unparseable": 0}
        log_stage("test_inmet_bulk", rows_in=3 * 3, rows_out=df.height, dropped=dropped)

    def test_sentinel_and_empty_both_missing(self):
        _, df, dropped, _ = parse_station_csv(make_csv(), "test-0")
        h1 = df.filter(df["valid_time"] == dt.datetime(2026, 1, 1, 1, tzinfo=dt.UTC))
        assert set(h1["variable"]) == {"precip_1h"}  # t2m/-9999 and wind/empty dropped
        assert dropped["value_missing"] == 2

    def test_unparseable_value_counted(self):
        csv = make_csv(data_lines=["2026/01/01;0000 UTC;abc;;19,9;1,1;"])
        _, df, dropped, _ = parse_station_csv(csv, "test-0")
        assert dropped["value_unparseable"] == 1
        assert df.height == 2

    def test_header_matched_without_accents(self):
        """Older vintages drop accents; matching is normalized, not literal."""
        header = (
            "Data;Hora UTC;PRECIPITACAO TOTAL, HORARIO (mm);RADIACAO GLOBAL (Kj/m2);"
            "TEMPERATURA DO AR - BULBO SECO, HORARIA (C);VENTO, VELOCIDADE HORARIA (m/s);"
        )
        _, df, _, _ = parse_station_csv(make_csv(header=header), "test-0")
        assert set(df["variable"]) == {"precip_1h", "t2m", "wind10m"}

    def test_ambiguous_header_raises(self):
        header = HEADER + "TEMPERATURA DO AR - BULBO SECO, DUPLICADA (°C);"
        with pytest.raises(ValueError, match="found 2"):
            parse_station_csv(make_csv(header=header), "test-0")

    def test_missing_required_column_raises(self):
        header = "Data;Hora UTC;PRECIPITAÇÃO TOTAL, HORÁRIO (mm);"
        with pytest.raises(ValueError, match="found 0"):
            parse_station_csv(make_csv(header=header), "test-0")

    def test_missing_station_code_raises(self):
        csv = make_csv().replace("CODIGO (WMO):;A001", "CODIGO (WMO):;")
        with pytest.raises(ValueError, match="CODIGO"):
            parse_station_csv(csv, "test-0")


class TestRowsFromZip:
    def make_zip(self, tmp_path, stations=("A001", "A042")):
        path = tmp_path / "2026.zip"
        with zipfile.ZipFile(path, "w") as zf:
            for sid in stations:
                name = f"INMET_CO_DF_{sid}_NOME COM ESPACO (X)_01-01-2026_A_30-06-2026.CSV"
                zf.writestr(name, make_csv(native_id=sid).encode("latin-1"))
        return path

    def test_concatenates_stations(self, tmp_path):
        df, total, per_station, _ = rows_from_zip(self.make_zip(tmp_path), "test-0")
        assert set(df["station_id"]) == {"inmet:A001", "inmet:A042"}
        assert set(per_station) == {"A001", "A042"}
        assert total["skipped_station_files"] == 0

    def test_station_filter_skips_with_accounting(self, tmp_path):
        df, total, per_station, _ = rows_from_zip(
            self.make_zip(tmp_path), "test-0", station_filter={"A001"}
        )
        assert set(df["station_id"]) == {"inmet:A001"}
        assert total["skipped_station_files"] == 1
        assert "A042" not in per_station

    def test_zip_reconciliation(self, tmp_path):
        """Line count is independent of emission — the identity is not circular."""
        df, total, _, n_lines = rows_from_zip(self.make_zip(tmp_path), "test-0")
        assert n_lines == 2 * 3  # 2 stations x 3 data lines
        row_drops = {k: v for k, v in total.items() if k != "skipped_station_files"}
        log_stage("test_inmet_bulk_zip", rows_in=n_lines * 3, rows_out=df.height, dropped=row_drops)
