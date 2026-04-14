from antrack.tracking.scan_cross import estimate_cross_offset, generate_cross_points
from antrack.tracking.scan_grid import generate_grid_points
from antrack.tracking.scan_session import ScanSession
from antrack.tracking.scan_spiral import generate_spiral_points, spiral_samples_to_grid


def test_generate_grid_points_uses_zigzag_order():
    points = generate_grid_points(10.0, 20.0, 2.0, 2.0, 1.0, order="zigzag")
    assert len(points) == 9
    first_row = points[:3]
    second_row = points[3:6]
    assert [round(point["az"], 3) for point in first_row] == [9.0, 10.0, 11.0]
    assert [round(point["az"], 3) for point in second_row] == [11.0, 10.0, 9.0]


def test_generate_cross_points_and_estimate_offset():
    curves = generate_cross_points(100.0, 30.0, 2.0, 1.0)
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
