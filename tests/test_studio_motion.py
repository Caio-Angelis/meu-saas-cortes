from app.gui.studio_motion import (
    ease_out_back,
    ease_out_cubic,
    ease_out_quint,
    lerp,
    lerp_color,
    sample_gradient,
)


def test_lerp_endpoints_and_midpoint():
    assert lerp(0.0, 10.0, 0.0) == 0.0
    assert lerp(0.0, 10.0, 1.0) == 10.0
    assert lerp(0.0, 10.0, 0.5) == 5.0


def test_easings_endpoints():
    for fn in (ease_out_cubic, ease_out_quint, ease_out_back):
        assert abs(fn(0.0)) < 1e-9
        assert abs(fn(1.0) - 1.0) < 1e-9


def test_ease_out_back_overshoots():
    assert ease_out_back(0.7) > 1.0


def test_ease_out_cubic_monotonic():
    values = [ease_out_cubic(t / 10.0) for t in range(11)]
    assert values == sorted(values)


def test_lerp_color_endpoints():
    assert lerp_color("#000000", "#FFFFFF", 0.0) == "#000000"
    assert lerp_color("#000000", "#FFFFFF", 1.0) == "#FFFFFF"


def test_lerp_color_midpoint_grey():
    mid = lerp_color("#000000", "#FFFFFF", 0.5).upper()
    assert mid in ("#808080", "#7F7F7F")


def test_lerp_color_channels_independent():
    assert lerp_color("#FF0000", "#0000FF", 0.0) == "#FF0000"
    assert lerp_color("#FF0000", "#0000FF", 1.0) == "#0000FF"


def test_sample_gradient_stops():
    stops = ("#000000", "#808080", "#FFFFFF")
    assert sample_gradient(stops, 0.0) == "#000000"
    assert sample_gradient(stops, 1.0) == "#FFFFFF"
    assert sample_gradient(("#123456",), 0.5) == "#123456"


def test_sample_gradient_clamps_out_of_range():
    stops = ("#000000", "#FFFFFF")
    assert sample_gradient(stops, -0.5) == "#000000"
    assert sample_gradient(stops, 1.5) == "#FFFFFF"
