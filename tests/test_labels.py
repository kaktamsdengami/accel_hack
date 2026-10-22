import pytest

from app.contracts import Side, SurfaceType
from app.labels import surface_label
from eval.metrics import pred_label
from workers import aggregate_logic

CASES = [
    ("led_perimeter", "far", None, "LED perimeter · far"),
    ("led_perimeter", "unknown", None, "LED perimeter"),
    ("static_board", "near", "A", "Board A · near"),
    ("static_board", "near", None, "Board · near"),
    ("shirt", "unknown", "home:chest", "Home shirt · chest"),
    ("shirt", "unknown", "7", "Shirt · 7"),
    ("backdrop", "left", None, "Backdrop"),
    ("banner", "left", None, "Banner · left"),
    ("other", "unknown", None, "Other"),
]


@pytest.mark.parametrize("st,side,board,expected", CASES)
def test_surface_label(st, side, board, expected):
    assert surface_label(st, side, board) == expected
    # enums and strings are interchangeable
    assert surface_label(SurfaceType(st), Side(side), board) == expected


@pytest.mark.parametrize("st,side,board,expected", CASES)
def test_eval_and_dashboard_print_the_same_string(st, side, board, expected):
    item = {"surface_type": st, "side": side, "board_id": board}
    assert pred_label(item) == expected
    assert aggregate_logic.surface_label(st, side, board) == expected  # re-export


def test_pred_label_explicit_label_and_unknown_type():
    assert pred_label({"surface_label": "My label", "surface_type": "shirt"}) == "My label"
    assert pred_label({}) == ""
    assert pred_label({"surface_type": "hologram"}) == "hologram"
    assert pred_label({"surface_type": "led_perimeter", "side": None}) == "LED perimeter"
