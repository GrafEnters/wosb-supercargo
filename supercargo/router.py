"""Plan trade trips from the stored market snapshot.

Prices move as you trade: each bought batch raises the source price by the good's `step`,
each sold batch lowers the destination price by the same share. A trip is filled batch by batch,
always taking the next batch with the best profit per unit of weight, until the hold is full
or nothing profitable is left.
"""
import json
import math
import statistics
from dataclasses import dataclass, field
from pathlib import Path

from .tooltip import fix_decimal

GOODS_PATH = Path(__file__).resolve().parent / "goods.json"
HOLD = 40_000  # weight that fits in the hold at normal speed
HOLD_OVERLOAD = 100_000  # max weight with overload; the ship sails 2x slower
OVERLOAD_SLOWDOWN = 2.0
MAX_BATCHES = 60  # per good per trip, a safety cap


def load_goods(path: Path = GOODS_PATH) -> dict[str, dict]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in data.items() if not k.startswith("_")}


def sanitize(ports: dict[str, dict]) -> dict[str, dict]:
    """Copy of the price data with OCR slips repaired or dropped."""
    ports = {n: {**p, "goods": {g: dict(v) for g, v in p["goods"].items()}} for n, p in ports.items()}
    for p in ports.values():
        for v in p["goods"].values():
            v["buy"], v["sell"], _ = fix_decimal(v.get("buy"), v.get("sell"))
    # Cross-port check: a price 5x above the typical one for that good is a lost decimal comma.
    by_good: dict[str, list[dict]] = {}
    for p in ports.values():
        for g, v in p["goods"].items():
            by_good.setdefault(g, []).append(v)
    for g, quotes in by_good.items():
        buys = [q["buy"] for q in quotes if q.get("buy")]
        if len(buys) < 3:
            continue
        med = statistics.median(buys)
        for q in quotes:
            for k in ("buy", "sell"):
                if q.get(k) and q[k] > 5 * med:
                    q[k] /= 10
    # Goods seen in only one port are usually OCR garbage (and give no deals anyway).
    rare = {g for g, quotes in by_good.items() if len(quotes) < 2}
    for p in ports.values():
        for g in rare:
            p["goods"].pop(g, None)
    return ports


@dataclass
class CargoItem:
    good: str
    units: int = 0
    batches: int = 0
    cost: float = 0.0
    revenue: float = 0.0
    weight: float = 0.0
    first_buy: float = 0.0
    last_buy: float = 0.0
    first_sell: float = 0.0
    last_sell: float = 0.0

    @property
    def profit(self) -> float:
        return self.revenue - self.cost


@dataclass
class Plan:
    capacity: float
    items: list[CargoItem] = field(default_factory=list)  # most profitable first

    @property
    def profit(self) -> float:
        return sum(i.profit for i in self.items)

    @property
    def cost(self) -> float:
        return sum(i.cost for i in self.items)

    @property
    def weight(self) -> float:
        return sum(i.weight for i in self.items)


def plan_trip(src: dict, dst: dict, goods: dict[str, dict], capacity: float) -> Plan:
    """Best cargo for one trip src -> dst within `capacity` weight."""
    batches = []  # (profit per weight, good, units, buy price, sell price)
    for name, sq in src["goods"].items():
        cfg = goods.get(name)
        dq = dst["goods"].get(name)
        if not cfg or not cfg.get("tradable", True) or not cfg.get("batch") or not dq:
            continue
        buy, sell = sq.get("buy"), dq.get("sell")
        if not buy or not sell:
            continue
        size, weight, step = cfg["batch"], cfg["weight"], cfg.get("step", 0.05)
        stock = sq.get("stock")
        left = stock if stock else size * MAX_BATCHES
        for k in range(MAX_BATCHES):
            if left <= 0:
                break
            b, s = buy * (1 + step) ** k, sell * (1 - step) ** k
            if s <= b:
                break
            units = min(size, left)
            left -= units
            batches.append(((s - b) / weight, name, units, b, s))
    batches.sort(key=lambda x: x[0], reverse=True)

    items: dict[str, CargoItem] = {}
    room = capacity
    for _, name, units, b, s in batches:
        weight = goods[name]["weight"]
        take = min(units, int(room // weight))
        if take <= 0:
            continue
        it = items.setdefault(name, CargoItem(name, first_buy=b, first_sell=s))
        it.units += take
        it.batches += 1
        it.cost += take * b
        it.revenue += take * s
        it.weight += take * weight
        it.last_buy, it.last_sell = b, s
        room -= take * weight
    return Plan(capacity, sorted(items.values(), key=lambda i: i.profit, reverse=True))


@dataclass
class Route:
    src: str
    dst: str
    distance: float | None  # map grid cells, straight line
    plan: Plan  # normal load
    overload: Plan  # max load, 2x slower

    @property
    def per_cell(self) -> float:
        return self.plan.profit / max(self.distance or 0, 0.5)

    @property
    def overload_better(self) -> bool:
        """Is sailing overloaded more profitable per unit of time?"""
        return self.overload.profit / OVERLOAD_SLOWDOWN > self.plan.profit * 1.02


SORT_KEYS = {
    "trip": lambda r: r.plan.profit,
    "distance": lambda r: r.per_cell,
}


def find_routes(ports: dict[str, dict], sort: str = "trip", goods: dict | None = None,
                hold: float = HOLD, hold_overload: float = HOLD_OVERLOAD) -> list[Route]:
    goods = goods or load_goods()
    ports = sanitize(ports)
    routes = []
    for src, sp in ports.items():
        for dst, dp in ports.items():
            if src == dst:
                continue
            plan = plan_trip(sp, dp, goods, hold)
            if plan.profit <= 0:
                continue
            dist = math.dist(sp["map_xy"], dp["map_xy"]) if sp.get("map_xy") and dp.get("map_xy") else None
            routes.append(Route(src, dst, dist, plan, plan_trip(sp, dp, goods, hold_overload)))
    return sorted(routes, key=SORT_KEYS[sort], reverse=True)


def money(x: float) -> str:
    return f"{x / 1000:.1f}k" if abs(x) >= 1000 else f"{x:.0f}"


def format_routes(routes: list[Route], limit: int = 10) -> str:
    if not routes:
        return "Выгодных рейсов пока нет - нужно снять цены хотя бы с двух портов."
    out = []
    for r in routes[:limit]:
        dist = f"{r.distance:.1f} кл." if r.distance else "?"
        out.append(f"{r.src} -> {r.dst}  {dist}  прибыль {money(r.plan.profit)}, вложить {money(r.plan.cost)}")
        for it in r.plan.items:
            out.append(f"    {it.good:<12} {it.units:>7} шт ({it.batches} парт.)  "
                       f"{it.first_buy:g}..{it.last_buy:.3g} -> {it.first_sell:g}..{it.last_sell:.3g}  +{money(it.profit)}")
    return "\n".join(out)
