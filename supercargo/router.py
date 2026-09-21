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
class Leg:
    """One hop: buy in src, sail, sell in dst."""
    src: str
    dst: str
    distance: float | None  # map grid cells along the sailed course
    plan: Plan  # normal load
    overload: Plan  # max load, 2x slower


@dataclass
class Route:
    """One or two hops in a row: A->B, or A->B->C where the money from the first hop pays for the second."""
    legs: list[Leg]
    approach: float | None = None  # cells from the ship's current position to the first port

    @property
    def src(self) -> str:
        return self.legs[0].src

    @property
    def dst(self) -> str:
        return self.legs[-1].dst

    @property
    def ports(self) -> list[str]:
        return [self.legs[0].src] + [leg.dst for leg in self.legs]

    @property
    def profit(self) -> float:
        return sum(leg.plan.profit for leg in self.legs)

    @property
    def distance(self) -> float | None:
        known = [leg.distance for leg in self.legs if leg.distance is not None]
        return sum(known) if known else None

    @property
    def sail(self) -> float:
        """Everything the ship has to sail before the last cargo is sold."""
        return (self.distance or 0.0) + (self.approach or 0.0)

    @property
    def per_cell(self) -> float:
        return self.profit / max(self.sail, 0.5)

    @property
    def capital(self) -> float:
        """Most money needed at once: later hops are paid for with what the earlier ones earned."""
        need = banked = 0.0
        for leg in self.legs:
            need = max(need, leg.plan.cost - banked)
            banked += leg.plan.profit
        return need

    @property
    def overload_profit(self) -> float:
        return sum(leg.overload.profit for leg in self.legs)

    @property
    def overload_better(self) -> bool:
        """Is sailing overloaded more profitable per unit of time?"""
        return self.overload_profit / OVERLOAD_SLOWDOWN > self.profit * 1.02


SORT_KEYS = {
    "trip": lambda r: r.profit,
    "distance": lambda r: r.per_cell,
}
TOP_ROUTES = 60  # how many routes are built in full; the rest never make it to the manifest


def find_routes(ports: dict[str, dict], sort: str = "trip", goods: dict | None = None,
                hold: float = HOLD, hold_overload: float = HOLD_OVERLOAD, nav=None,
                legs: int = 1) -> list[Route]:
    """nav (navigation.Navigator) makes the courses sail around shallow water the ship may not enter
    and adds the leg from the ship's current position; without it distances are straight lines.
    legs=2 also chains two hops, so the cargo bought with the first hop's money is planned too."""
    goods = goods or load_goods()
    ports = sanitize(ports)
    names = [n for n in ports if nav is None or nav.reachable(n)]
    # None when the ship cannot get there at all (it may be sitting inside a zone it must leave first)
    approach = {n: nav.approach(n) for n in names} if nav and nav.ship_xy else {}

    hops: dict[tuple[str, str], Leg] = {}
    for src in names:
        sp = ports[src]
        for dst in names:
            if src == dst:
                continue
            dp = ports[dst]
            if nav is not None:
                dist = nav.distance(src, dst)
                if dist is None:  # no way around the shallows for this ship
                    continue
            elif sp.get("map_xy") and dp.get("map_xy"):
                dist = math.dist(sp["map_xy"], dp["map_xy"])
            else:
                dist = None
            plan = plan_trip(sp, dp, goods, hold)
            if plan.profit > 0:
                hops[(src, dst)] = Leg(src, dst, dist, plan, plan_trip(sp, dp, goods, hold_overload))

    by_source: dict[str, list[Leg]] = {}
    for leg in hops.values():
        by_source.setdefault(leg.src, []).append(leg)

    # Score plain numbers first and only build the best routes: two hops over 40 ports is ~60k combinations.
    per_cell = sort == "distance"

    def score(profit, sail):
        return profit / max(sail, 0.5) if per_cell else profit

    scored = []
    for (src, dst), leg in hops.items():
        start = approach.get(src) or 0.0
        scored.append((score(leg.plan.profit, start + (leg.distance or 0)), src, dst, None))
        if legs > 1:
            for nxt in by_source.get(dst, ()):
                profit = leg.plan.profit + nxt.plan.profit
                sail = start + (leg.distance or 0) + (nxt.distance or 0)
                scored.append((score(profit, sail), src, dst, nxt.dst))
    scored.sort(key=lambda x: x[0], reverse=True)

    routes = []
    for _, src, dst, third in scored[:TOP_ROUTES]:
        chain = [hops[(src, dst)]] + ([hops[(dst, third)]] if third else [])
        routes.append(Route(chain, approach.get(src)))
    return routes


def money(x: float) -> str:
    return f"{x / 1000:.1f}k" if abs(x) >= 1000 else f"{x:.0f}"


def format_routes(routes: list[Route], limit: int = 10) -> str:
    if not routes:
        return "Выгодных рейсов пока нет - нужно снять цены хотя бы с двух портов."
    out = []
    for r in routes[:limit]:
        legs = " -> ".join(r.ports)
        parts = []
        if r.approach:
            parts.append(f"подход {r.approach:.1f}")
        if r.distance is not None:
            parts.append(f"путь {r.distance:.1f} кл.")
        out.append(f"{legs}  {', '.join(parts)}  прибыль {money(r.profit)}, вложить {money(r.capital)}")
        for leg in r.legs:
            out.append(f"  {leg.src} -> {leg.dst}:")
            for it in leg.plan.items:
                out.append(f"    {it.good:<12} {it.units:>7} шт ({it.batches} парт.)  "
                           f"{it.first_buy:g}..{it.last_buy:.3g} -> {it.first_sell:g}..{it.last_sell:.3g}  "
                           f"+{money(it.profit)}")
    return "\n".join(out)
