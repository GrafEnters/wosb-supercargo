"""Find profitable buy-here/sell-there deals from the stored market snapshot."""
import math
import time
from dataclasses import dataclass


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
    distance: float | None  # map pixels between the two port icons
    age_min: float  # age of the older of the two quotes

    @property
    def per_distance(self) -> float | None:
        return self.profit / self.distance * 100 if self.distance else None


def find_deals(ports: dict[str, dict], apply_tax: bool = False) -> list[Deal]:
    now = time.time()
    deals = []
    for src, sp in ports.items():
        for dst, dp in ports.items():
            if src == dst:
                continue
            dist = None
            if sp.get("map_pos") and dp.get("map_pos"):
                dist = math.dist(sp["map_pos"], dp["map_pos"])
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


def format_deals(deals: list[Deal], sort: str = "margin", limit: int = 10) -> str:
    if not deals:
        return "Выгодных сделок пока нет - нужно снять цены хотя бы с двух портов."
    deals = sorted(deals, key=SORT_KEYS[sort], reverse=True)[:limit]
    out = [f"{'Товар':<13}{'Купить в':<22}{'Продать в':<22}{'Цена':>12}{'Прибыль/шт':>12}{'Маржа':>8}{'Дист.':>7}{'Возраст':>9}"]
    for d in deals:
        dist = f"{d.distance:.0f}" if d.distance else "?"
        out.append(
            f"{d.good[:12]:<13}{d.src[:21]:<22}{d.dst[:21]:<22}{f'{d.buy:g} -> {d.sell:g}':>12}"
            f"{d.profit:>12.2f}{d.margin:>7.0%}{dist:>7}{d.age_min:>7.0f}м"
        )
    return "\n".join(out)
