"""The ONE place that turns (surface_type, side, board_id) into the human label shown everywhere.

Used by the aggregation worker (SurfaceStats / LedSlot labels), the API and the eval metrics, so a
prediction and the dashboard print identical strings. Examples: "LED perimeter · far",
"Board A · near", "Home shirt · chest". Pure, no I/O.
"""
from __future__ import annotations

from app.contracts import Side, SurfaceType


def surface_label(surface_type: SurfaceType | str, side: Side | str | None = Side.unknown,
                  board_id: str | None = None) -> str:
    """Human label for a surface.

    Shirts and backdrops carry no side. For a shirt, board_id may be "home:chest" -> "Home shirt · chest";
    any other board_id gives "Shirt · <board_id>". A static board shows its id: "Board A · near".
    """
    st = SurfaceType(surface_type)
    sd = Side(side or Side.unknown)
    side_part = "" if sd == Side.unknown else f" · {sd.value}"
    if st == SurfaceType.led_perimeter:
        return f"LED perimeter{side_part}"
    if st == SurfaceType.static_board:
        return (f"Board {board_id}" if board_id else "Board") + side_part
    if st == SurfaceType.shirt:
        if board_id and ":" in board_id:
            kit, place = board_id.split(":", 1)
            return f"{kit.capitalize()} shirt · {place}"
        return f"Shirt · {board_id}" if board_id else "Shirt"
    if st == SurfaceType.backdrop:
        return "Backdrop"
    if st == SurfaceType.banner:
        return "Banner" + side_part
    return "Other" + side_part
