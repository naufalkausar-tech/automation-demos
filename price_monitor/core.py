"""Competitor price monitoring: read prices, match products, suggest repricing inside a margin floor.

Prices are read the way shoppers see them: Western or Arabic-Indic digits, Arabic decimal/thousands separators,
any currency symbol or word around the number. Product matching is a suggestion step only: difflib scores
normalised Arabic/English names and a person approves each pair before it is used.

Margin floor: min_margin_pct is gross margin on the selling price, (price - cost) / price, so the lowest
allowed price is cost / (1 - min_margin_pct / 100). A suggestion never goes below it.
"""
from __future__ import annotations

import csv
import math
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path

# Arabic-Indic (U+0660..) and Extended Arabic-Indic / Persian (U+06F0..) digits, Arabic decimal and thousands separators
_TO_ASCII = {**{0x0660 + i: str(i) for i in range(10)}, **{0x06F0 + i: str(i) for i in range(10)},
             0x066B: ".", 0x066C: ",", 0x060C: ","}
# a number with thousands/decimal groups; a space only groups 3 digits that are not followed by a letter/digit and
# come before any '.'/',' group ("1 299,00" yes; "12.99 500 sold" and "45 100ml" stop at 12.99 and 45)
_NUMBER = re.compile(r"\d+(?:\s\d{3}(?![^\W_]))*(?:[.,]\d+)*")
_TASHKEEL = re.compile(r"[ؐ-ًؚ-ٰٟۖ-ۭـ]")  # vowel marks + tatweel
_ARABIC_FOLD = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ى": "ي", "ة": "ه", "ؤ": "و", "ئ": "ي"})
_TOKENS = re.compile(r"\d+(?:\.\d+)?|[^\W\d_]+")
# \b on the stems: "منفذ البيع" (sales outlet) must not read as نفذ, "menghabiskan" not as habis
OUT_OF_STOCK = re.compile(r"out of stock|sold out|unavailable|not available|not in stock|\bنفد|\bنفذ|غير متوفر|غير متاح"
                          r"|\bhabis|tidak tersedia")
IN_STOCK = re.compile(r"in stock|available|add to (?:cart|basket)|متوفر|متاح|اضف الي السله|tersedia")
YES = {"yes", "y", "true", "1", "x", "ok", "نعم", "ya"}


def _fold(text: str) -> str:
    """NFKC, ASCII digits, no Arabic vowel marks, lower case: the form every comparison here works on."""
    text = unicodedata.normalize("NFKC", text or "").translate(_TO_ASCII)
    return _TASHKEEL.sub("", text).translate(_ARABIC_FOLD).casefold()


def parse_price(text: str | None, decimal: str = ".") -> float | None:
    """'£51.77', 'SAR 1,299.00', '١٬٢٩٩٫٠٠ ر.س', '1.299,00 €' -> float. None when there is no number.

    With both '.' and ',' present the rightmost one is the decimal point; with only one kind, `decimal`
    (per source) decides, so '1,299' is 1299 on a '.'-decimal site and 1.299 on a ','-decimal one.
    """
    if text is None:
        return None
    match = _NUMBER.search(unicodedata.normalize("NFKC", str(text)).translate(_TO_ASCII))
    if not match:
        return None
    number = re.sub(r"\s", "", match.group())
    if "." in number and "," in number:
        decimal = "." if number.rfind(".") > number.rfind(",") else ","
    number = number.replace("," if decimal == "." else ".", "")
    if number.count(decimal) > 1:
        return None
    return float(number.replace(decimal, "."))


def parse_availability(text: str | None) -> bool | None:
    """True in stock, False out of stock, None when the page does not say."""
    folded = _fold(text or "")
    if OUT_OF_STOCK.search(folded):      # checked first: "غير متوفر" contains "متوفر"
        return False
    if IN_STOCK.search(folded):
        return True
    return None


def normalise_name(name: str) -> str:
    """Comparable product name: folded Arabic letters, no 'ال' prefix, numbers split from units ('500ml' -> '500 ml')."""
    words = _TOKENS.findall(_fold(name))
    return " ".join(w[2:] if w.startswith("ال") and len(w) > 3 else w for w in words)


def similarity(a: str, b: str) -> float:
    """0..1 for two normalised names. Word order is ignored; differing numbers (size, model) halve the score,
    which keeps such a pair strictly under 0.5 (the names differ, so the raw score is below 1)."""
    if not a or not b:
        return 0.0
    ta, tb = a.split(), b.split()
    score = max(SequenceMatcher(None, a, b).ratio(),
                SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio())
    na, nb = {t for t in ta if t[0].isdigit()}, {t for t in tb if t[0].isdigit()}
    if na and nb and na != nb:
        score /= 2
    return score


def _cents(value: float, up: bool = False) -> float:
    cents = round(value * 100, 6)            # 0.29 * 100 = 28.999999999999996
    return (math.ceil(cents) if up else math.floor(cents)) / 100


@dataclass
class Product:
    sku: str
    name: str
    our_price: float
    cost: float
    min_margin_pct: float

    @property
    def floor(self) -> float:
        return _cents(self.cost / (1 - self.min_margin_pct / 100), up=True)


@dataclass
class Offer:
    competitor: str
    name: str
    price: float | None
    in_stock: bool | None
    url: str = ""
    page_url: str = ""
    raw_price: str = ""

    @property
    def key(self) -> str:
        """Identity across runs: the product URL, or competitor + normalised name when cards have no link."""
        return self.url or f"{self.competitor}|{normalise_name(self.name)}"


@dataclass
class Match:
    sku: str
    competitor: str = ""
    url: str = ""
    name: str = ""


def _read_csv(path: str | Path, required: set[str]) -> list[dict[str, str]]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"{path}: missing column(s) {', '.join(sorted(missing))}")
        return [{k: (v or "").strip() for k, v in row.items() if k} for row in reader]


def load_catalogue(path: str | Path) -> list[Product]:
    products, seen = [], set()
    for line, row in enumerate(_read_csv(path, {"sku", "name", "our_price", "cost", "min_margin_pct"}), start=2):
        if not row["sku"]:
            continue
        if row["sku"] in seen:          # two rows for one SKU would put two prices for it in the import CSV
            raise ValueError(f"{path} line {line}: duplicate sku {row['sku']}")
        seen.add(row["sku"])
        numbers = {k: parse_price(row[k]) for k in ("our_price", "cost", "min_margin_pct")}
        if None in numbers.values():
            raise ValueError(f"{path} line {line}: our_price, cost and min_margin_pct must be numbers")
        if not 0 <= numbers["min_margin_pct"] < 100:
            raise ValueError(f"{path} line {line}: min_margin_pct must be 0-99 (gross margin on the selling price)")
        products.append(Product(row["sku"], row["name"], **numbers))
    return products


def load_matches(path: str | Path) -> list[Match]:
    """Match table: sku + competitor_url or competitor_name (+ optional competitor).

    A file with an `approved` column (e.g. an edited match_suggestions.csv) only contributes approved rows;
    a hand-written table without that column is taken as approved as a whole.
    """
    rows = _read_csv(path, {"sku"})
    gated = bool(rows) and "approved" in rows[0]
    return [Match(r["sku"], r.get("competitor", ""), r.get("competitor_url", ""), r.get("competitor_name", ""))
            for r in rows
            if r["sku"] and (r.get("competitor_url") or r.get("competitor_name"))
            and (not gated or r["approved"].casefold() in YES)]


def suggest_matches(products: list[Product], offers: list[Offer], matches: list[Match] = (),
                    min_score: float = 0.6) -> list[dict]:
    """Best-scoring offer per SKU and competitor, for SKUs not matched to that competitor yet. `approved` is left blank."""
    done = {(m.sku, m.competitor) for m in matches} | {(m.sku, "") for m in matches if not m.competitor}
    by_competitor: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        by_competitor[offer.competitor].append(offer)
    rows = []
    for product in products:
        ours = normalise_name(product.name)
        for competitor, candidates in by_competitor.items():
            if (product.sku, competitor) in done or (product.sku, "") in done:
                continue
            score, best = max(((similarity(ours, normalise_name(o.name)), o) for o in candidates), key=lambda t: t[0])
            if score >= min_score:
                rows.append({"sku": product.sku, "our_name": product.name, "competitor": competitor,
                             "competitor_name": best.name, "competitor_url": best.url,
                             "score": round(score, 3), "approved": ""})
    return rows


def resolve(matches: list[Match], offers: list[Offer]) -> tuple[dict[str, list[Offer]], list[Match]]:
    """Approved matches -> this run's offers per SKU, plus the matches whose product was not seen this run."""
    by_url = {o.url: o for o in offers if o.url}
    by_name: dict[str, list[Offer]] = defaultdict(list)
    for offer in offers:
        by_name[normalise_name(offer.name)].append(offer)
    linked: dict[str, list[Offer]] = defaultdict(list)
    missing = []
    for m in matches:
        if m.url:
            found = by_url.get(m.url)
        else:
            found = next((o for o in by_name[normalise_name(m.name)] if not m.competitor or o.competitor == m.competitor), None)
        if found is None:
            missing.append(m)
        elif found not in linked[m.sku]:
            linked[m.sku].append(found)
    return dict(linked), missing


@dataclass
class Settings:
    undercut_pct: float = 0.0       # 0 = match the lowest competitor
    undercut_amount: float = 0.0
    cheaper_pct: float = 10.0       # flag when we sit this far below the lowest competitor
    error_pct: float = 50.0         # a competitor price that moved this much, or sits this far below our cost, is suspect
    auto_approve_pct: float = 0.0   # pre-approve changes up to this size; 0 = a person approves every change


FLAG_LABELS = {
    "price_changed": "Competitor price changed since last run",
    "competitor_out_of_stock": "Competitor out of stock",
    "possible_pricing_error": "Possible competitor pricing error (ignored)",
    "we_are_cheaper": "We are much cheaper than the lowest competitor",
    "floor_limited": "Target is below our margin floor",
    "below_floor": "Our current price is below the margin floor",
    "no_competitor_price": "No usable competitor price",
}
BLOCKS_AUTO_APPROVE = {"possible_pricing_error"}


@dataclass
class Suggestion:
    product: Product
    offers: list[Offer]
    target: Offer | None = None
    suggested: float | None = None
    action: str = "hold"            # raise / lower / keep / hold (no usable competitor price)
    flags: list[tuple[str, str]] = field(default_factory=list)
    approve: bool = False

    @property
    def gap_pct(self) -> float | None:
        """How far our price is above (+) or below (-) the lowest usable competitor price."""
        return self.product.our_price / self.target.price - 1 if self.target else None

    @property
    def change_pct(self) -> float | None:
        return self.suggested / self.product.our_price - 1 if self.suggested is not None and self.product.our_price else None


def pricing_error(price: float, before: float | None, cost: float, error_pct: float) -> str | None:
    limit = error_pct / 100
    if before and abs(price - before) / before >= limit:
        return f"moved {price / before - 1:+.0%} since last run ({before:.2f} -> {price:.2f})"
    if cost and price < cost * (1 - limit):
        return f"{price:.2f} is less than {1 - limit:.0%} of our cost {cost:.2f}"
    return None


def suggest(product: Product, offers: list[Offer], previous: dict[str, tuple[float | None, bool | None]],
            settings: Settings = Settings()) -> Suggestion:
    """Suggested price = lowest usable competitor price (minus undercut), never below the margin floor."""
    s = Suggestion(product, offers)
    usable = []
    for offer in offers:
        before = (previous.get(offer.key) or (None, None))[0]
        if before is not None and offer.price is not None and abs(offer.price - before) >= 0.005:
            s.flags.append(("price_changed", f"{offer.competitor}: {before:.2f} -> {offer.price:.2f}"))
        if offer.in_stock is False:
            s.flags.append(("competitor_out_of_stock", f"{offer.competitor} is out of stock"))
            continue
        if not offer.price:
            continue
        reason = pricing_error(offer.price, before, product.cost, settings.error_pct)
        if reason:
            s.flags.append(("possible_pricing_error", f"{offer.competitor}: {reason}"))
            continue
        usable.append(offer)
    floor = product.floor
    if product.our_price < floor:
        s.flags.append(("below_floor", f"our {product.our_price:.2f} is under the floor {floor:.2f}"))
    if not usable:
        s.flags.append(("no_competitor_price", "no in-stock, plausible competitor price this run" if offers
                        else "no matched competitor offer this run"))
        return s
    s.target = min(usable, key=lambda o: o.price)
    target = _cents(s.target.price * (1 - settings.undercut_pct / 100) - settings.undercut_amount)
    if target < floor:
        s.flags.append(("floor_limited", f"target {target:.2f} is below the floor {floor:.2f}; holding the floor"))
    s.suggested = max(target, floor)
    if s.gap_pct <= -settings.cheaper_pct / 100:
        s.flags.append(("we_are_cheaper", f"we are {-s.gap_pct:.0%} below {s.target.competitor}"))
    change = s.suggested - product.our_price
    s.action = "keep" if abs(change) < 0.005 else "raise" if change > 0 else "lower"
    s.approve = (s.action in ("raise", "lower") and s.change_pct is not None
                 and abs(s.change_pct) * 100 <= settings.auto_approve_pct
                 and not any(code in BLOCKS_AUTO_APPROVE for code, _ in s.flags))
    return s
