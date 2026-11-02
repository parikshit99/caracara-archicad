"""A small spatial index over 3D bounding boxes, in plain Python.

Answers "which elements of A are within d meters of some element of B" and "which elements
of A sit inside an element of B" without comparing every pair: boxes are filed in a 2D grid
of cells, and only boxes sharing a cell are compared. Boxes that would span too many cells
(a slab under the whole building) are kept aside and compared with everything, which keeps
the grid small. Built per question from data Archicad already computed; nothing is kept.

Bounding boxes are an approximation of the real shapes: a diagonal wall or an L-shaped room
has a box larger than itself. "Inside" can also test a container's plan outline, with its holes,
where Archicad gives one. Exact collision checks are left to Archicad (GetCollisions).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field

# A box covering more cells than this is compared with every box instead of being filed.
MAX_CELLS_PER_BOX = 64


@dataclass(frozen=True)
class Box:
    key: str
    x_min: float
    y_min: float
    z_min: float
    x_max: float
    y_max: float
    z_max: float

    def distance(self, other: Box) -> float:
        """Shortest distance between the two boxes; 0 when they touch or overlap."""
        dx = max(0.0, other.x_min - self.x_max, self.x_min - other.x_max)
        dy = max(0.0, other.y_min - self.y_max, self.y_min - other.y_max)
        dz = max(0.0, other.z_min - self.z_max, self.z_min - other.z_max)
        return math.sqrt(dx * dx + dy * dy + dz * dz)

    def center(self) -> tuple[float, float, float]:
        return ((self.x_min + self.x_max) / 2, (self.y_min + self.y_max) / 2, (self.z_min + self.z_max) / 2)

    def contains_point(self, point: tuple[float, float, float]) -> bool:
        x, y, z = point
        return self.x_min <= x <= self.x_max and self.y_min <= y <= self.y_max and self.z_min <= z <= self.z_max


Point2D = tuple[float, float]


# How far from an edge, in meters, a point still counts as lying on it.
EDGE_TOLERANCE = 1e-9


def _on_edge(point: Point2D, start: Point2D, end: Point2D) -> bool:
    (x, y), (x1, y1), (x2, y2) = point, start, end
    cross = (x2 - x1) * (y - y1) - (y2 - y1) * (x - x1)
    within = min(x1, x2) - EDGE_TOLERANCE <= x <= max(x1, x2) + EDGE_TOLERANCE
    return abs(cross) <= EDGE_TOLERANCE and within and min(y1, y2) - EDGE_TOLERANCE <= y <= max(y1, y2) + EDGE_TOLERANCE


def _side(point: Point2D, ring: list[Point2D]) -> int:
    """1 when the point is inside the closed ring, 0 when it lies on its edge, -1 when it is outside."""
    x, y = point
    inside = False
    for start, end in zip(ring, ring[1:] + ring[:1], strict=True):
        if _on_edge(point, start, end):
            return 0
        (x1, y1), (x2, y2) = start, end
        # Count the edges a ray to the right of the point crosses.
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return 1 if inside else -1


@dataclass(frozen=True)
class Outline:
    """A plan polygon with straight edges and its holes. A point on an edge counts as inside."""

    ring: list[Point2D]
    holes: list[list[Point2D]] = field(default_factory=list)

    def contains(self, point: Point2D) -> bool:
        return _side(point, self.ring) >= 0 and all(_side(point, hole) <= 0 for hole in self.holes)


class GridIndex:
    """Boxes filed by the 2D grid cells (x, y) they cover, each grown by a margin."""

    def __init__(self, boxes: Iterable[Box], margin: float = 0.0):
        self.boxes = list(boxes)
        self.keys = {box.key for box in self.boxes}
        self.margin = margin
        self.cell = self._cell_size(self.boxes, margin)
        self.cells: dict[tuple[int, int], list[Box]] = defaultdict(list)
        self.oversized: list[Box] = []
        for box in self.boxes:
            cells = list(self._cells_of(box, margin))
            if len(cells) > MAX_CELLS_PER_BOX:
                self.oversized.append(box)
            else:
                for cell in cells:
                    self.cells[cell].append(box)

    @staticmethod
    def _cell_size(boxes: list[Box], margin: float) -> float:
        """About the size of a typical box, so most boxes fall into a handful of cells."""
        if not boxes:
            return 1.0
        extents = sorted(max(box.x_max - box.x_min, box.y_max - box.y_min) for box in boxes)
        return max(extents[len(extents) // 2], 2 * margin, 0.1)

    def _cells_of(self, box: Box, margin: float) -> Iterator[tuple[int, int]]:
        x0, x1 = math.floor((box.x_min - margin) / self.cell), math.floor((box.x_max + margin) / self.cell)
        y0, y1 = math.floor((box.y_min - margin) / self.cell), math.floor((box.y_max + margin) / self.cell)
        if (x1 - x0 + 1) * (y1 - y0 + 1) > MAX_CELLS_PER_BOX:
            # Report "too many" without listing them all.
            yield from ((x0 + i, y0) for i in range(MAX_CELLS_PER_BOX + 1))
            return
        for x in range(x0, x1 + 1):
            for y in range(y0, y1 + 1):
                yield (x, y)

    def candidates(self, box: Box) -> Iterator[Box]:
        """Every filed box that may lie within the margin of the given box, each once."""
        seen: set[str] = set()
        cells = list(self._cells_of(box, 0.0))
        if len(cells) > MAX_CELLS_PER_BOX:
            candidates: Iterable[Box] = self.boxes
        else:
            candidates = (other for cell in cells for other in self.cells.get(cell, ()))
        for other in [*candidates, *self.oversized]:
            if other.key not in seen:
                seen.add(other.key)
                yield other


@dataclass
class Proximity:
    """The result of pairs_within: totals and matched keys for all pairs, the nearest ones in full."""

    pair_count: int
    matched: set[str]
    other_matched: set[str]
    nearest: list[tuple[Box, Box, float]]


def pairs_within(boxes: list[Box], others: list[Box], distance: float, keep: int) -> Proximity:
    """Every pair (box, other) whose boxes are at most `distance` apart; the `keep` nearest are listed.

    A pair of two elements that are in both lists is counted once.
    """
    keys = {box.key for box in boxes}
    index = GridIndex(others, margin=distance)
    result = Proximity(0, set(), set(), [])
    for box in boxes:
        for other in index.candidates(box):
            if other.key == box.key or (other.key in keys and other.key < box.key and box.key in index.keys):
                continue
            gap = box.distance(other)
            if gap <= distance:
                result.pair_count += 1
                result.matched.add(box.key)
                result.other_matched.add(other.key)
                result.nearest.append((box, other, gap))
                if len(result.nearest) > 4 * keep:
                    result.nearest = sorted(result.nearest, key=lambda pair: pair[2])[:keep]
    result.nearest = sorted(result.nearest, key=lambda pair: pair[2])[:keep]
    return result


def containers_of(
    boxes: list[Box], containers: list[Box], outlines: dict[str, Outline] | None = None
) -> dict[str, list[str]]:
    """For each box, the containers that hold its center point: within the container's box and, where the
    container has a plan outline, within that outline."""
    index = GridIndex(containers)
    outlines = outlines or {}
    inside: dict[str, list[str]] = {}
    for box in boxes:
        center = box.center()
        point = Box(box.key, *center, *center)
        holders = [
            other.key
            for other in index.candidates(point)
            if other.contains_point(center)
            and (other.key not in outlines or outlines[other.key].contains((center[0], center[1])))
        ]
        if holders:
            inside[box.key] = holders
    return inside
