"""The spatial index must find exactly what comparing every pair finds, only faster."""

from __future__ import annotations

import random

import pytest

from caracara_archicad_mcp import spatial
from caracara_archicad_mcp.spatial import Box, GridIndex, Outline, containers_of, pairs_within


def _random_boxes(rng: random.Random, count: int, prefix: str, slabs: int = 0) -> list[Box]:
    boxes = []
    for i in range(count):
        x, y, z = rng.uniform(0, 100), rng.uniform(0, 100), rng.uniform(0, 20)
        boxes.append(
            Box(f"{prefix}{i}", x, y, z, x + rng.uniform(0.1, 6), y + rng.uniform(0.1, 6), z + rng.uniform(0.1, 3))
        )
    # Slabs spanning the whole site exercise the boxes kept outside the grid.
    boxes += [Box(f"{prefix}slab{i}", -5, -5, 3 * i, 110, 110, 3 * i + 0.3) for i in range(slabs)]
    return boxes


def test_distance_between_boxes():
    a = Box("a", 0, 0, 0, 1, 1, 1)
    assert a.distance(Box("b", 0.5, 0.5, 0.5, 2, 2, 2)) == 0  # overlapping
    assert a.distance(Box("b", 1, 0, 0, 2, 1, 1)) == 0  # touching
    assert a.distance(Box("b", 4, 0, 0, 5, 1, 1)) == 3
    assert a.distance(Box("b", 4, 5, 0, 5, 6, 1)) == 5  # 3-4-5 triangle


@pytest.mark.parametrize("seed", range(5))
@pytest.mark.parametrize("distance", [0.0, 0.5, 3.0])
def test_near_matches_comparing_every_pair(seed, distance):
    rng = random.Random(seed)
    boxes, others = _random_boxes(rng, 200, "a"), _random_boxes(rng, 150, "b", slabs=2)

    found = pairs_within(boxes, others, distance, keep=5)

    expected = sorted((a.distance(b), a.key, b.key) for a in boxes for b in others if a.distance(b) <= distance)
    assert found.pair_count == len(expected)
    assert found.matched == {a for _, a, _ in expected}
    assert found.other_matched == {b for _, _, b in expected}
    assert [gap for _, _, gap in found.nearest] == [gap for gap, _, _ in expected[:5]]


def test_a_set_compared_with_itself_counts_each_pair_once():
    boxes = [Box("a", 0, 0, 0, 1, 1, 1), Box("b", 1.2, 0, 0, 2, 1, 1), Box("c", 9, 9, 9, 10, 10, 10)]
    found = pairs_within(boxes, boxes, 0.5, keep=10)
    assert found.pair_count == 1
    assert found.matched | found.other_matched == {"a", "b"}


@pytest.mark.parametrize("seed", range(5))
def test_inside_matches_checking_every_container(seed):
    rng = random.Random(seed)
    boxes, rooms = _random_boxes(rng, 300, "f"), _random_boxes(rng, 60, "z", slabs=1)

    inside = containers_of(boxes, rooms)

    expected = {b.key: sorted(r.key for r in rooms if r.contains_point(b.center())) for b in boxes}
    assert {key: sorted(value) for key, value in inside.items()} == {k: v for k, v in expected.items() if v}


def test_huge_boxes_stay_out_of_the_grid():
    small = [Box(f"small{i}", i, 0, 0, i + 1, 1, 1) for i in range(10)]
    index = GridIndex([Box("site", -1000, -1000, 0, 1000, 1000, 1), *small])
    assert [box.key for box in index.oversized] == ["site"]
    assert sum(len(cell) for cell in index.cells.values()) <= len(small) * spatial.MAX_CELLS_PER_BOX


def test_empty_inputs():
    assert pairs_within([], [Box("b", 0, 0, 0, 1, 1, 1)], 1.0, keep=5).pair_count == 0
    assert containers_of([Box("a", 0, 0, 0, 1, 1, 1)], []) == {}


# An L-shaped room: 6 x 6 m with the 4 x 4 m corner at the top right cut away.
L_SHAPE = [(0, 0), (6, 0), (6, 2), (2, 2), (2, 6), (0, 6)]


@pytest.mark.parametrize(
    ("point", "inside"),
    [
        ((1, 1), True),
        ((5, 1), True),
        ((1, 5), True),
        ((4, 4), False),  # in the cut-away corner, though inside the room's bounding box
        ((2, 4), True),  # on an edge
        ((6, 2), True),  # on a corner
        ((7, 1), False),
    ],
)
def test_outline_tells_an_l_shaped_room_from_its_box(point, inside):
    assert Outline(L_SHAPE).contains(point) is inside


def test_outline_leaves_out_its_holes_but_not_their_edges():
    slab = Outline([(0, 0), (10, 0), (10, 10), (0, 10)], holes=[[(4, 4), (6, 4), (6, 6), (4, 6)]])
    assert slab.contains((2, 2))
    assert not slab.contains((5, 5))
    assert slab.contains((4, 5))


def test_inside_uses_a_containers_outline_where_it_has_one():
    room = Box("room", 0, 0, 0, 6, 6, 3)
    in_arm, in_corner, too_high = (
        Box("arm", 0.5, 0.5, 0, 1.5, 1.5, 1),
        Box("corner", 3.5, 3.5, 0, 4.5, 4.5, 1),
        Box("high", 0.5, 0.5, 5, 1.5, 1.5, 6),
    )

    assert set(containers_of([in_arm, in_corner, too_high], [room])) == {"arm", "corner"}
    assert set(containers_of([in_arm, in_corner, too_high], [room], {"room": Outline(L_SHAPE)})) == {"arm"}
