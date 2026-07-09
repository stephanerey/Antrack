from pathlib import Path

from antrack.tracking import satellites
from antrack.tracking.satellites import TLERepository


SAMPLE_TLE = """ISS (ZARYA)
1 25544U 98067A   20357.54791667  .00001264  00000-0  29663-4 0  9990
2 25544  51.6463  21.3856 0002189  89.5472  41.0380 15.49314927256344
"""


def test_tle_repository_uses_cache_when_download_times_out(tmp_path: Path, monkeypatch):
    cache_file = tmp_path / "celestrak_stations.tle"
    cache_file.write_text(SAMPLE_TLE, encoding="utf-8")
    observed = {}

    def fake_urlopen(_url, *, timeout, context=None):
        observed["timeout"] = timeout
        raise TimeoutError("simulated timeout")

    monkeypatch.setattr(satellites, "urlopen", fake_urlopen)
    repo = TLERepository(
        str(tmp_path),
        groups=["stations"],
        refresh_hours=6.0,
        download_timeout_s=3.5,
    )

    repo.refresh_if_due(force=True)

    assert observed["timeout"] == 3.5
    assert "ISS (ZARYA)" in repo.by_name
    assert repo.by_norad[25544].name == "ISS (ZARYA)"
