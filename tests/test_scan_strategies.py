from antrack.tracking.scan_cross import estimate_cross_offset, generate_cross_points
from antrack.tracking.scan_grid import generate_grid_points
from antrack.tracking.scan_peak import estimate_four_point_divergence_peak
from antrack.tracking.scan_results import make_scan_result, make_scan_sample, scan_error_series
from antrack.tracking.scan_session import ScanSession
from antrack.tracking.scan_spiral import generate_spiral_points, spiral_samples_to_grid


def test_generate_grid_points_uses_zigzag_order():
    points = generate_grid_points(10.0, 20.0, 2.0, 2.0, 1.0, order="zigzag")
    assert len(points) == 9
    first_row = points[:3]
    second_row = points[3:6]
    assert [round(point["az"], 3) for point in first_row] == [9.0, 10.0, 11.0]
    assert [round(point["az"], 3) for point in second_row] == [11.0, 10.0, 9.0]
    assert [round(point["relative_az_deg"], 3) for point in first_row] == [-1.0, 0.0, 1.0]
    assert [round(point["relative_el_deg"], 3) for point in first_row] == [-1.0, -1.0, -1.0]


def test_generate_cross_points_and_estimate_offset():
    curves = generate_cross_points(100.0, 30.0, 2.0, 1.0)
    assert [point["relative_el_deg"] for point in curves["azimuth"]] == [0.0, 0.0, 0.0]
    assert [point["relative_az_deg"] for point in curves["elevation"]] == [0.0, 0.0, 0.0]
    az_curve = [{**point, "value": value} for point, value in zip(curves["azimuth"], [1.0, 5.0, 2.0])]
    el_curve = [{**point, "value": value} for point, value in zip(curves["elevation"], [0.5, 1.0, 4.0])]
    result = estimate_cross_offset(az_curve, el_curve, 100.0, 30.0)
    assert result["az_offset_deg"] == 0.0
    assert result["el_offset_deg"] == 1.0


def test_generate_spiral_points_respect_requested_span():
    points = generate_spiral_points(0.0, 0.0, 4.0, 0.5, turns=3)
    assert points
    max_radius = max(point["radius"] for point in points)
    assert max_radius <= 2.0 + 1e-6
    assert all(abs(point["relative_az_deg"] - point["az"]) <= 1e-9 for point in points)
    assert all(abs(point["relative_el_deg"] - point["el"]) <= 1e-9 for point in points)


def test_spiral_samples_to_grid_projects_values():
    samples = [
        {"az": -0.5, "el": -0.5, "value": 1.0},
        {"az": 0.5, "el": -0.5, "value": 2.0},
        {"az": -0.5, "el": 0.5, "value": 3.0},
        {"az": 0.5, "el": 0.5, "value": 4.0},
    ]
    heatmap = spiral_samples_to_grid(samples, 1.0)
    assert heatmap["grid"].shape == (2, 2)
    assert heatmap["grid"][1, 1] == 4.0


def test_scan_sample_records_theoretical_center_and_offsets():
    sample = make_scan_sample(
        {"az": 11.0, "el": 19.5, "phase": "coarse"},
        42.0,
        theoretical_az_deg=10.0,
        theoretical_el_deg=20.0,
        timestamp=123.0,
    )

    assert sample["az"] == 11.0
    assert sample["el"] == 19.5
    assert sample["value"] == 42.0
    assert sample["timestamp"] == 123.0
    assert sample["theoretical_az"] == 10.0
    assert sample["theoretical_el"] == 20.0
    assert sample["offset_az"] == 1.0
    assert sample["offset_el"] == -0.5


def test_scan_result_exposes_peak_estimate_and_error_trace():
    samples = [
        make_scan_sample({"az": 9.5, "el": 20.0}, 1.0, theoretical_az_deg=10.0, theoretical_el_deg=20.0),
        make_scan_sample({"az": 10.5, "el": 20.5}, 5.0, theoretical_az_deg=10.0, theoretical_el_deg=20.0),
    ]

    result = make_scan_result(strategy="grid", samples=samples, center_az_deg=10.0, center_el_deg=20.0)

    assert result["best_point"] == samples[1]
    assert result["peak_estimate"]["method"] == "best_sample"
    assert result["az_offset_deg"] == 0.5
    assert result["el_offset_deg"] == 0.5
    assert result["error_trace"][0]["angular_error_deg"] > 0.7


def test_scan_result_offsets_follow_peak_theoretical_center():
    samples = [
        make_scan_sample({"az": 101.0, "el": 29.0}, 5.0, theoretical_az_deg=100.0, theoretical_el_deg=30.0),
    ]

    result = make_scan_result(strategy="grid", samples=samples, center_az_deg=0.0, center_el_deg=0.0)

    assert result["az_offset_deg"] == 1.0
    assert result["el_offset_deg"] == -1.0


def test_scan_error_series_prepares_plot_values():
    series = scan_error_series(
        [
            {"az_error_deg": 1.0, "el_error_deg": -2.0, "angular_error_deg": 2.5},
            {"az_error_deg": -0.5, "el_error_deg": 0.25},
        ]
    )

    assert series["x"] == [0.0, 1.0]
    assert series["az_error_deg"] == [1.0, -0.5]
    assert series["el_error_deg"] == [-2.0, 0.25]
    assert series["angular_error_deg"][0] == 2.5
    assert series["angular_error_deg"][1] > 0.55


def test_four_point_divergence_peak_estimates_inside_best_cell():
    samples = [
        make_scan_sample({"az": 0.0, "el": 0.0}, 1.0, theoretical_az_deg=0.0, theoretical_el_deg=0.0),
        make_scan_sample({"az": 1.0, "el": 0.0}, 2.0, theoretical_az_deg=0.0, theoretical_el_deg=0.0),
        make_scan_sample({"az": 0.0, "el": 1.0}, 3.0, theoretical_az_deg=0.0, theoretical_el_deg=0.0),
        make_scan_sample({"az": 1.0, "el": 1.0}, 5.0, theoretical_az_deg=0.0, theoretical_el_deg=0.0),
    ]

    peak = estimate_four_point_divergence_peak(samples)

    assert peak is not None
    assert peak["method"] == "four_point_divergence"
    assert 0.0 < peak["az"] < 1.0
    assert 0.0 < peak["el"] < 1.0
    assert peak["confidence"] > 0.25
    assert peak["cell"]["az_min"] == 0.0


def test_scan_session_recovers_synthetic_offset():
    current = {"az": 0.0, "el": 0.0}
    true_peak = {"az": 1.0, "el": -0.5}

    def move_to(az_deg: float, el_deg: float) -> None:
        current["az"] = az_deg
        current["el"] = el_deg

    def measure(_config: dict) -> float:
        return -((current["az"] - true_peak["az"]) ** 2 + (current["el"] - true_peak["el"]) ** 2)

    session = ScanSession(thread_manager=None, move_to=move_to, measure=measure)
    session._run(
        {
            "strategy": "grid",
            "center_az_deg": 0.0,
            "center_el_deg": 0.0,
            "span_deg": 4.0,
            "step_deg": 0.5,
            "settle_s": 0.0,
            "integration_s": 0.01,
        }
    )
    result = session.latest_result
    assert result is not None
    assert abs(result["az_offset_deg"] - 1.0) <= 0.5
    assert abs(result["el_offset_deg"] + 0.5) <= 0.5
    assert result["peak_estimate"]["method"] == "best_sample"
    assert result["error_trace"]
    assert all("theoretical_az" in sample for sample in result["samples"])
    assert all("offset_az" in sample for sample in result["samples"])
    assert all("relative_az_deg" in sample for sample in result["samples"])


def test_scan_session_emits_progress_stages_during_point_measurement():
    progress = []

    session = ScanSession(
        thread_manager=None,
        move_to=lambda az_deg, el_deg: None,
        wait_for_settle=lambda az_deg, el_deg, settle_s: None,
        measure=lambda _config: 1.0,
    )
    session.progress_updated.connect(progress.append)

    session._measure_point(
        {"az": 1.0, "el": 2.0, "relative_az_deg": 0.1, "relative_el_deg": -0.1},
        {"center_az_deg": 1.0, "center_el_deg": 2.0, "_progress_current": 3, "_progress_total": 9},
    )

    assert [snapshot["stage"] for snapshot in progress] == ["move", "settle", "measure", "done"]
    assert all(snapshot["current"] == 3 for snapshot in progress)
    assert all(snapshot["total"] == 9 for snapshot in progress)


def test_scan_session_includes_telemetry_snapshot_in_sample():
    session = ScanSession(
        thread_manager=None,
        move_to=lambda az_deg, el_deg: None,
        wait_for_settle=lambda az_deg, el_deg, settle_s: None,
        telemetry_provider=lambda: {"actual_az": 12.5, "actual_el": 34.5, "set_az": 13.0, "set_el": 35.0},
        measure=lambda _config: 7.0,
    )

    sample = session._measure_point(
        {"az": 13.0, "el": 35.0, "relative_az_deg": 1.0, "relative_el_deg": 2.0},
        {"center_az_deg": 12.0, "center_el_deg": 33.0, "_progress_current": 1, "_progress_total": 4},
    )

    assert sample["actual_az"] == 12.5
    assert sample["actual_el"] == 34.5
    assert sample["set_az"] == 13.0
    assert sample["set_el"] == 35.0
    assert sample["requested_offset_az"] == 1.0
    assert sample["requested_offset_el"] == 2.0
    assert sample["actual_offset_az"] == 0.5
    assert sample["actual_offset_el"] == 1.5
    assert sample["offset_error_az"] == -0.5
    assert sample["offset_error_el"] == -0.5


def test_scan_session_can_use_four_point_peak_estimator():
    current = {"az": 0.0, "el": 0.0}

    def move_to(az_deg: float, el_deg: float) -> None:
        current["az"] = az_deg
        current["el"] = el_deg

    def measure(_config: dict) -> float:
        return current["az"] + current["el"]

    session = ScanSession(thread_manager=None, move_to=move_to, measure=measure)
    session._run(
        {
            "strategy": "grid",
            "peak_estimator": "four_point_divergence",
            "center_az_deg": 0.0,
            "center_el_deg": 0.0,
            "span_deg": 2.0,
            "step_deg": 2.0,
            "settle_s": 0.0,
            "integration_s": 0.01,
        }
    )

    result = session.latest_result
    assert result is not None
    assert result["peak_estimate"]["method"] == "four_point_divergence"
    assert -1.0 < result["az_offset_deg"] < 1.0
    assert -1.0 < result["el_offset_deg"] < 1.0


def test_scan_session_can_materialize_points_around_dynamic_center():
    centers = iter([(100.0, 30.0), (100.5, 30.25), (101.0, 30.5), (101.5, 30.75)])
    moves = []

    def center_provider():
        return next(centers)

    def move_to(az_deg: float, el_deg: float) -> None:
        moves.append((az_deg, el_deg))

    session = ScanSession(
        thread_manager=None,
        center_provider=center_provider,
        move_to=move_to,
        measure=lambda _config: 1.0,
    )
    session._run(
        {
            "strategy": "grid",
            "center_mode": "tracking_relative",
            "center_az_deg": 0.0,
            "center_el_deg": 0.0,
            "span_deg": 2.0,
            "step_deg": 2.0,
            "settle_s": 0.0,
            "integration_s": 0.01,
        }
    )

    result = session.latest_result
    assert result is not None
    assert moves[0] == (99.0, 29.0)
    assert result["samples"][0]["theoretical_az"] == 100.0
    assert result["samples"][0]["theoretical_el"] == 30.0
    assert result["samples"][0]["offset_az"] == -1.0
    assert result["samples"][1]["theoretical_az"] == 100.5


def test_scan_session_prefers_enriched_move_callback_signature():
    calls = []

    def move_to(*, point: dict, config: dict) -> None:
        calls.append((point["az"], point["el"], config["center_mode"]))

    session = ScanSession(
        thread_manager=None,
        move_to=move_to,
        measure=lambda _config: 1.0,
    )
    session._run(
        {
            "strategy": "grid",
            "center_mode": "tracking_relative",
            "center_az_deg": 10.0,
            "center_el_deg": 20.0,
            "span_deg": 0.0,
            "step_deg": 1.0,
            "settle_s": 0.0,
            "integration_s": 0.01,
        }
    )

    assert calls
    assert all(call == (10.0, 20.0, "tracking_relative") for call in calls)
