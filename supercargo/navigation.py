"""Sailing distances that go around shallow water.

Ship ranks run I (largest) .. VII (smallest). A shallow zone marked VI means "ranks VI-VII only",
so a ship of rank R may enter a zone of rank X when R >= X. The same rule applies to ports:
their tooltip says "Мелководье IV-VII ранги".

Shortest paths are computed on a visibility graph: the corners of the blocking zones plus the ports.
Between two points that see each other the ship sails straight, so the shortest route around the
polygons always bends over their corners.
"""
import heapq
import json
import math
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

ZONES_PATH = Path(__file__).resolve().parent / "shallows.json"
MAX_RANK = 7
ROMAN = ["", "I", "II", "III", "IV", "V", "VI", "VII"]
EPS = 1e-9


def roman(rank: int) -> str:
    return ROMAN[rank] if 0 < rank <= MAX_RANK else str(rank)


def zone_label(rank: int) -> str:
    """How the game words it: a zone of rank VI admits ranks VI-VII."""
    return f"{roman(rank)}–{roman(MAX_RANK)}" if rank < MAX_RANK else roman(MAX_RANK)


def _roman_to_int(token: str) -> int | None:
    # OCR mixes up strokes: 1/l/| are I, \/ is V, Cyrillic Н/И/П read as II.
    token = token.upper().replace("\\/", "V").replace("|", "I")
    token = re.sub(r"[1LI]", "I", token)
    token = re.sub(r"[НИПHN]", "II", token)
    token = re.sub(r"[VУY]", "V", token)
    if not re.fullmatch(r"[IV]+", token or ""):
        return None
    values = {"I": 1, "V": 5}
    total = 0
    for i, ch in enumerate(token):
        v = values[ch]
        total += -v if i + 1 < len(token) and values[token[i + 1]] > v else v
    return total if 0 < total <= MAX_RANK else None


def min_rank(shallow) -> int | None:
    """Smallest allowed ship rank at a port / in a zone. None = no limit."""
    if shallow is None or isinstance(shallow, int):
        return shallow
    for token in re.split(r"[-–—\s:]+", str(shallow)):
        rank = _roman_to_int(token)
        if rank:
            return rank
    return None


PEACE = "peace"  # a zone closed to ships flying the peace flag (the central circle, for one)
SHALLOW = "shallow"


@dataclass
class Zone:
    rank: int  # ranks rank..VII may enter; 0 for peace zones
    points: list[list[float]]  # map cells
    kind: str = SHALLOW

    def blocks(self, ship_rank: int, peace: bool) -> bool:
        return self.rank > ship_rank if self.kind == SHALLOW else peace

    def label(self) -> str:
        return "мирный флаг" if self.kind == PEACE else zone_label(self.rank)

    @property
    def xy(self) -> np.ndarray:
        return np.asarray(self.points, dtype=float)

    def centroid(self) -> tuple[float, float]:
        p = self.xy
        return float(p[:, 0].mean()), float(p[:, 1].mean())


@dataclass
class Rules:
    """What the map forbids: shallow zones, peace-flag zones, and ports closed to the peace flag."""
    zones: list[Zone]
    peace_ports: set[str]


def load_rules(path: Path = ZONES_PATH) -> Rules:
    if not path.exists():
        return Rules([], set())
    data = json.loads(path.read_text(encoding="utf-8"))
    zones = [Zone(z.get("rank", 0), z["points"], z.get("kind", SHALLOW)) for z in data.get("zones", [])]
    return Rules(zones, set(data.get("peace_ports", [])))


def save_rules(rules: Rules, path: Path = ZONES_PATH):
    data = {
        "zones": [{"rank": z.rank, "kind": z.kind,
                   "points": [[round(x, 3), round(y, 3)] for x, y in z.points]} for z in rules.zones],
        "peace_ports": sorted(rules.peace_ports),
    }
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def _cross(u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """2D cross product (numpy 2 dropped np.cross for 2-vectors)."""
    return u[..., 0] * v[..., 1] - u[..., 1] * v[..., 0]


def points_in_polygon(points: np.ndarray, poly: np.ndarray) -> np.ndarray:
    """Ray casting for a batch of points, shape (..., 2) -> bool of the same leading shape."""
    flat = points.reshape(-1, 2)
    x, y = flat[:, 0:1], flat[:, 1:2]
    ax, ay = poly[:, 0][None, :], poly[:, 1][None, :]
    bx, by = np.roll(poly[:, 0], -1)[None, :], np.roll(poly[:, 1], -1)[None, :]
    straddles = (ay > y) != (by > y)
    with np.errstate(divide="ignore", invalid="ignore"):
        cross_x = ax + (y - ay) * (bx - ax) / np.where(by - ay == 0, np.nan, by - ay)
    hits = np.where(straddles & (x < cross_x), 1, 0).sum(axis=1)
    return (hits % 2 == 1).reshape(points.shape[:-1])


def suggest_rank(zone: Zone, port_ranks: dict[tuple[float, float], int]) -> int | None:
    """Rank of a zone read off the ports inside it: the water has to admit the largest ship
    any of them accepts, so the least strict port wins."""
    if zone.kind == PEACE:
        return None
    inside = [rank for xy, rank in port_ranks.items()
              if rank and points_in_polygon(np.asarray([xy]), zone.xy)[0]]
    return min(inside) if inside else None


class Navigator:
    """Distances between ports for one ship rank, going around the zones it may not enter."""

    SAMPLES = 7  # points along a segment tested for being inside a zone

    def __init__(self, zones: list[Zone], ship_rank: int, ports: dict[str, tuple[float, float]],
                 port_ranks: dict[str, int | None] | None = None, peace: bool = False,
                 peace_ports: set[str] | None = None):
        self.ship_rank = ship_rank
        self.peace = peace
        self.peace_ports = set(peace_ports or ())
        self.blocking = [z for z in zones if z.blocks(ship_rank, peace)]
        self.polys = [z.xy for z in self.blocking]
        self.names = list(ports)
        port_ranks = port_ranks or {}

        coords = [ports[n] for n in self.names]
        self.n_ports = len(coords)
        corners = [tuple(p) for poly in self.polys for p in poly]
        self.nodes = np.asarray(coords + corners, dtype=float) if (coords or corners) else np.zeros((0, 2))
        self.index = {n: i for i, n in enumerate(self.names)}

        # A port is off limits when its own shallow rank is stricter than the ship,
        # or when it sits inside a zone the ship may not enter.
        self.blocked_ports = set()
        for name in self.names:
            rank = port_ranks.get(name)
            inside = any(points_in_polygon(np.asarray([ports[name]]), poly)[0] for poly in self.polys)
            if (rank and rank > ship_rank) or inside or (peace and name in self.peace_ports):
                self.blocked_ports.add(name)

        self._edges()
        self._shortest_paths()
        self.ship_xy = None
        self.ship_vis = None

    # ---------- visibility ----------
    def _edges(self):
        if self.polys:
            starts = np.concatenate([p for p in self.polys])
            ends = np.concatenate([np.roll(p, -1, axis=0) for p in self.polys])
        else:
            starts = ends = np.zeros((0, 2))
        self.edge_a, self.edge_b = starts, ends

    def visible_from(self, point: np.ndarray, targets: np.ndarray) -> np.ndarray:
        """Which of `targets` the ship can reach from `point` in a straight line."""
        if len(targets) == 0:
            return np.zeros(0, dtype=bool)
        if not self.polys:
            return np.ones(len(targets), dtype=bool)
        p = np.asarray(point, dtype=float)
        d = targets - p  # (T, 2)
        ea, eb = self.edge_a, self.edge_b  # (M, 2)
        # strict crossing test: both segments must straddle each other
        d1 = _cross(d[:, None, :], ea[None, :, :] - p)
        d2 = _cross(d[:, None, :], eb[None, :, :] - p)
        e = eb - ea
        d3 = _cross(e[None, :, :], p - ea[None, :, :])
        d4 = _cross(e[None, :, :], targets[:, None, :] - ea[None, :, :])
        crossing = ((d1 * d2 < -EPS) & (d3 * d4 < -EPS)).any(axis=1)
        # a segment may also run through a concave pocket without cutting any edge
        ts = np.linspace(0.06, 0.94, self.SAMPLES)[None, :, None]
        samples = p + d[:, None, :] * ts  # (T, S, 2)
        inside = np.zeros(len(targets), dtype=bool)
        for poly in self.polys:
            inside |= points_in_polygon(samples, poly).any(axis=1)
        return ~(crossing | inside)

    def _shortest_paths(self):
        n = len(self.nodes)
        inf = math.inf
        w = np.full((n, n), inf)
        for i in range(n):
            vis = self.visible_from(self.nodes[i], self.nodes)
            d = np.hypot(*(self.nodes - self.nodes[i]).T)
            w[i] = np.where(vis, d, inf)
            w[i, i] = 0.0
        self.weights = w
        # Floyd-Warshall (a few hundred nodes; numpy keeps it quick), with first hops for drawing.
        d = w.copy()
        nxt = np.tile(np.arange(n), (n, 1))
        nxt[~np.isfinite(w)] = -1
        for k in range(n):
            cand = d[:, k, None] + d[None, k, :]
            better = cand < d - 1e-12
            d = np.where(better, cand, d)
            nxt = np.where(better, nxt[:, k][:, None], nxt)
        self.dist_matrix, self.next_hop = d, nxt

    # ---------- queries ----------
    def reachable(self, name: str) -> bool:
        return name in self.index and name not in self.blocked_ports

    def distance(self, a: str, b: str) -> float | None:
        if not (self.reachable(a) and self.reachable(b)):
            return None
        d = self.dist_matrix[self.index[a], self.index[b]]
        return float(d) if math.isfinite(d) else None

    def path(self, a: str, b: str) -> list[tuple[float, float]]:
        """Waypoints of the route, ports included."""
        if self.distance(a, b) is None:
            return []
        i, j = self.index[a], self.index[b]
        out = [tuple(self.nodes[i])]
        guard = 0
        while i != j and guard < 500:
            i = int(self.next_hop[i, j])
            if i < 0:
                return []
            out.append(tuple(self.nodes[i]))
            guard += 1
        return out

    # ---------- the ship's own position ----------
    def set_ship(self, xy):
        self.ship_xy = tuple(xy) if xy else None
        self.ship_vis = None
        if self.ship_xy is None or not len(self.nodes):
            return
        vis = self.visible_from(np.asarray(self.ship_xy, dtype=float), self.nodes)
        d = np.hypot(*(self.nodes - np.asarray(self.ship_xy)).T)
        self.ship_vis = np.where(vis, d, math.inf)

    def _ship_best(self, name: str):
        if self.ship_vis is None or not self.reachable(name):
            return None, None
        total = self.ship_vis + self.dist_matrix[:, self.index[name]]
        k = int(np.argmin(total))
        return (float(total[k]), k) if math.isfinite(total[k]) else (None, None)

    def approach(self, name: str) -> float | None:
        return self._ship_best(name)[0]

    def approach_path(self, name: str) -> list[tuple[float, float]]:
        total, k = self._ship_best(name)
        if total is None:
            return []
        out = [self.ship_xy, tuple(self.nodes[k])]
        i, j = k, self.index[name]
        guard = 0
        while i != j and guard < 500:
            i = int(self.next_hop[i, j])
            if i < 0:
                break
            out.append(tuple(self.nodes[i]))
            guard += 1
        return out


def dijkstra(weights: np.ndarray, source: int) -> list[float]:
    """Kept for tests: plain Dijkstra over the same weight matrix."""
    n = len(weights)
    dist = [math.inf] * n
    dist[source] = 0.0
    seen = [False] * n
    heap = [(0.0, source)]
    while heap:
        d, u = heapq.heappop(heap)
        if seen[u]:
            continue
        seen[u] = True
        for v in range(n):
            w = weights[u, v]
            if math.isfinite(w) and d + w < dist[v]:
                dist[v] = d + w
                heapq.heappush(heap, (dist[v], v))
    return dist
