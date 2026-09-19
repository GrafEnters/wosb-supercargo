"""Find profitable buy-here/sell-there deals from the stored market snapshot."""
import math
import statistics
import time
from dataclasses import dataclass

from .tooltip import fix_decimal


@dataclass
class Deal:
    good: str
    src: str
    dst: str
    buy: float
    sell: float
    profit: float  # per unit
    margin: float  # profit / buy
    stock: float | None  # available at source
    distance: float | None  # map grid cells between the ports (straight line)
    age_min: float  # age of the older of the two quotes

    @property
    def per_distance(self) -> float | None:
        return self.profit / self.distance if self.distance else None


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


def find_deals(ports: dict[str, dict], apply_tax: bool = False) -> list[Deal]:
    ports = sanitize(ports)
    now = time.time()
    deals = []
    for src, sp in ports.items():
        for dst, dp in ports.items():
            if src == dst:
                continue
            dist = None
            if sp.get("map_xy") and dp.get("map_xy"):
                dist = math.dist(sp["map_xy"], dp["map_xy"])
            for good, sg in sp["goods"].items():
                dg = dp["goods"].get(good)
                if not dg or sg.get("buy") is None or dg.get("sell") is None:
                    continue
                buy, sell = sg["buy"], dg["sell"]
                if apply_tax:
                    buy *= 1 + (sp.get("tax") or 0) / 100
                    sell *= 1 - (dp.get("tax") or 0) / 100
                profit = sell - buy
                if profit <= 0 or buy <= 0:
                    continue
                deals.append(Deal(
                    good, src, dst, buy, sell, profit, profit / buy, sg.get("stock"), dist,
                    (now - min(sp["updated"], dp["updated"])) / 60,
                ))
    return deals


SORT_KEYS = {
    "margin": lambda d: d.margin,
    "profit": lambda d: d.profit,
    "distance": lambda d: d.per_distance or 0,
}


@dataclass
class Route:
    src: str
    dst: str
    distance: float | None
    deals: list[Deal]  # best first

    @property
    def best(self) -> Deal:
        return self.deals[0]


def find_routes(ports: dict[str, dict], sort: str = "margin", apply_tax: bool = False) -> list[Route]:
    """Deals grouped by (src, dst) pair, pairs ordered by their best deal."""
    key = SORT_KEYS[sort]
    by_pair: dict[tuple[str, str], list[Deal]] = {}
    for d in find_deals(ports, apply_tax):
        by_pair.setdefault((d.src, d.dst), []).append(d)
    routes = [
        Route(src, dst, ds[0].distance, sorted(ds, key=key, reverse=True))
        for (src, dst), ds in by_pair.items()
    ]
    return sorted(routes, key=lambda r: key(r.best), reverse=True)


def format_deals(deals: list[Deal], sort: str = "margin", limit: int = 10) -> str:
    if not deals:
        return "Выгодных сделок пока нет - нужно снять цены хотя бы с двух портов."
    deals = sorted(deals, key=SORT_KEYS[sort], reverse=True)[:limit]
    out = [f"{'Товар':<13}{'Купить в':<22}{'Продать в':<22}{'Цена':>12}{'Прибыль/шт':>12}{'Маржа':>8}{'Дист.':>7}{'Возраст':>9}"]
    for d in deals:
        dist = f"{d.distance:.1f}" if d.distance else "?"
        out.append(
            f"{d.good[:12]:<13}{d.src[:21]:<22}{d.dst[:21]:<22}{f'{d.buy:g} -> {d.sell:g}':>12}"
            f"{d.profit:>12.2f}{d.margin:>7.0%}{dist:>7}{d.age_min:>7.0f}м"
        )
    return "\n".join(out)
