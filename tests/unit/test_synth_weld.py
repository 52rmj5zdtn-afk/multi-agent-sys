# -*- coding: utf-8 -*-
"""焊缝合成图像生成器与几何测量测试。"""
import random
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from services.weld_vision.synth_weld import (DEFECT_TYPES, GEO_LIMITS, IMG_SIZE,
                                             depth_to_gray, geometry_verdict, gray_to_depth,
                                             measure_bead, render_depth_image, render_weld_image)


def test_render_shapes_and_labels():
    rng = random.Random(42)
    img, depth, label = render_weld_image(rng, False)
    assert img.size == (IMG_SIZE, IMG_SIZE) and label == "good"
    assert depth.shape == (IMG_SIZE, IMG_SIZE) and depth.dtype == np.float32
    for d in DEFECT_TYPES:
        _, _, label = render_weld_image(rng, True, d)
        assert label == d


def test_unknown_defect_rejected():
    try:
        render_weld_image(random.Random(1), True, "not_a_defect")
    except ValueError:
        return
    raise AssertionError("未知缺陷类型应抛 ValueError")


def test_defect_changes_image():
    rng = random.Random(7)
    _, good, _ = render_weld_image(rng, False)
    _, bad, _ = render_weld_image(rng, True, "pore")
    assert not np.allclose(good, bad)


def test_depth_gray_roundtrip():
    _, depth, _ = render_weld_image(random.Random(8), False)
    gray = depth_to_gray(depth)
    assert gray.dtype == np.uint8 and gray.shape == depth.shape
    restored = gray_to_depth(gray)
    assert np.allclose(restored, depth, atol=0.5)   # 8bit 量化误差


def test_measure_normal_in_window():
    rng = random.Random(20)
    passed = 0
    for _ in range(30):
        _, depth, _ = render_weld_image(rng, False)
        m = measure_bead(depth)
        ok, _ = geometry_verdict(m)
        passed += ok
    assert passed >= 24, f"正常品几何通过率过低: {passed}/30"


def test_measure_catches_geometry_defects():
    rng = random.Random(21)
    for defect in ("excess_reinforcement", "lack_of_fusion"):
        flagged = sum(
            not geometry_verdict(measure_bead(render_weld_image(rng, True, defect)[1]))[0]
            for _ in range(20))
        assert flagged >= 12, f"{defect} 几何拦截率过低: {flagged}/20"


def test_geo_limits_have_three_keys():
    assert set(GEO_LIMITS) == {"bead_width_mm", "reinforcement_mm", "undercut_mm"}


def test_depth_image_saves(tmp_path):
    _, depth, _ = render_weld_image(random.Random(3), False)
    out = tmp_path / "d.png"
    render_depth_image(depth).save(out)
    assert out.exists() and out.stat().st_size > 1000
