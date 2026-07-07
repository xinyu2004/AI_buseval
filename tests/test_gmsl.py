"""Tests for GMSL link bandwidth calculator."""
import pytest

from buseval.gmsl.calculator import (
    calculate_link,
    parse_param_string,
    load_yaml,
    build_report_from_links,
)
from buseval.gmsl.report import build_gmsl_structured

EXAMPLES = __import__("pathlib").Path(__file__).resolve().parents[1] / "examples"


def test_gmsl_single_link_formula():
    """1920×1080×30×12×1.2×1.15×1.067 = 1099.2 Mbps."""
    r = calculate_link("TEST", width=1920, height=1080, fps=30, bpp=12)
    assert abs(r.pixel_rate_mbps - 746.496) < 1e-3
    assert abs(r.after_blanking_mbps - 895.7952) < 1e-3
    assert abs(r.after_encoding_mbps - 1030.16448) < 1e-3
    assert abs(r.link_bw_mbps - 1099.185502) < 1e-2
    assert r.best_fit == "gmsl1"


def test_gmsl_custom_blanking():
    r_default = calculate_link("T", width=1920, height=1080, fps=30, bpp=12)
    r_custom = calculate_link("T", width=1920, height=1080, fps=30, bpp=12, blanking=1.5)
    assert r_custom.link_bw_mbps > r_default.link_bw_mbps
    assert r_custom.blanking == 1.5


def test_gmsl_param_string_parse():
    params = parse_param_string("width=1920 height=1080 fps=30 bpp=12 blanking=1.25")
    assert params["width"] == 1920
    assert params["height"] == 1080
    assert params["fps"] == 30
    assert params["bpp"] == 12
    assert params["blanking"] == 1.25


def test_gmsl_param_string_comma_legacy():
    """Legacy comma-separated form still works."""
    params = parse_param_string("width=1920,height=1080,fps=30,bpp=12")
    assert params["width"] == 1920
    assert params["height"] == 1080
    assert params["fps"] == 30
    assert params["bpp"] == 12


def test_gmsl_param_string_mixed_separators():
    """Mixed comma and space separators work."""
    params = parse_param_string("width=1920 height=1080, fps=30, bpp=12")
    assert params["width"] == 1920
    assert params["fps"] == 30
    assert len(params) == 4


def test_gmsl_param_string_bad():
    with pytest.raises(ValueError):
        parse_param_string("width=1920,no_equals")


def test_gmsl_yaml_multi_link():
    links, overrides = load_yaml(EXAMPLES / "gmsl_links.yaml")
    assert len(links) == 4
    assert links[0]["name"] == "CAM_FRONT"
    # no global overrides in the example
    assert "blanking" not in overrides


def test_gmsl_yaml_global_blanking_override():
    """If YAML has top-level blanking, it applies to all links."""
    import tempfile, yaml as _yaml
    content = _yaml.dump({
        "blanking": 1.5,
        "links": [{"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12}],
    })
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(content)
        path = f.name
    links, overrides = load_yaml(path)
    assert overrides["blanking"] == 1.5
    report = build_report_from_links(links, overrides)
    assert report.links[0].blanking == 1.5


def test_gmsl_recommendation_table():
    """1099 Mbps → GMSL1 fits, GMSL2/3 also fit; best=GMSL1."""
    r = calculate_link("T", width=1920, height=1080, fps=30, bpp=12)
    assert len(r.recommendations) == 3
    assert r.recommendations[0]["tier"] == "gmsl1"
    assert r.recommendations[0]["fits"] is True
    assert r.recommendations[1]["fits"] is True
    assert r.best_fit == "gmsl1"


def test_gmsl_overflow_no_tier_fits():
    """8K@60×16bpp → exceeds all tiers."""
    r = calculate_link("HUGE", width=7680, height=4320, fps=60, bpp=16)
    assert r.link_bw_mbps > 6000  # > GMSL3
    assert all(not rec["fits"] for rec in r.recommendations)
    assert r.best_fit == ""


def test_gmsl_report_total():
    report = build_report_from_links([
        {"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12},
        {"name": "B", "width": 1280, "height": 720, "fps": 60, "bpp": 12},
    ])
    assert len(report.links) == 2
    expected_total = report.links[0].link_bw_mbps + report.links[1].link_bw_mbps
    assert abs(report.total_link_bw_mbps - expected_total) < 1e-3


def test_gmsl_structured_output():
    report = build_report_from_links([
        {"name": "A", "width": 1920, "height": 1080, "fps": 30, "bpp": 12},
    ])
    d = build_gmsl_structured(report)
    assert "links" in d
    assert "total_link_bw_mbps" in d
    assert "summary" in d
    assert d["links"][0]["name"] == "A"
    assert d["summary"]["link_count"] == 1


def test_gmsl_cli_single_link(capsys):
    from buseval.cli import main
    rc = main(["predict", "--GMSL", "width=1920", "height=1080", "fps=30", "bpp=12", "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert d["links"][0]["link_bw_mbps"] > 1000
    assert d["links"][0]["best_fit"] == "gmsl1"


def test_gmsl_cli_single_link_legacy_comma(capsys):
    """Legacy comma-separated form still works via CLI."""
    from buseval.cli import main
    rc = main(["predict", "--GMSL", "width=1920,height=1080,fps=30,bpp=12", "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert d["links"][0]["link_bw_mbps"] > 1000


def test_gmsl_cli_yaml_multi(capsys):
    from buseval.cli import main
    rc = main(["predict", "--GMSL", str(EXAMPLES / "gmsl_links.yaml"), "--format", "json"])
    assert rc == 0
    import json
    d = json.loads(capsys.readouterr().out)
    assert len(d["links"]) == 4
    assert d["summary"]["link_count"] == 4
