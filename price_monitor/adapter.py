"""Pushing approved prices to a store: the interface only. This package never calls a store API.

To automate the last step, implement StoreAdapter.push for the store platform and feed it
read_changes("approved_changes.csv"). Points each implementation must respect:
  - look products up by SKU (Shopify: variant SKU, then productVariantsBulkUpdate; WooCommerce REST:
    GET /wp-json/wc/v3/products?sku=..., then PUT /wp-json/wc/v3/products/<id> {"regular_price": "..."};
    Salla/Zid: product lookup by SKU, then their product update endpoint)
  - re-read the live price first and skip the row if it no longer equals current_price (someone changed it)
  - never send a price the CSV did not approve, log every call, and run DryRunAdapter first
"""
from __future__ import annotations

import csv
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class PriceChange:
    sku: str
    name: str
    current_price: float
    new_price: float


@dataclass
class PushResult:
    sku: str
    ok: bool
    message: str


class StoreAdapter(ABC):
    @abstractmethod
    def push(self, changes: list[PriceChange]) -> list[PushResult]:
        """Apply the changes in the store and report one result per change."""


class DryRunAdapter(StoreAdapter):
    """Records what would be sent; the reference behaviour for a real adapter's dry-run mode."""

    def __init__(self):
        self.sent: list[PriceChange] = []

    def push(self, changes: list[PriceChange]) -> list[PushResult]:
        self.sent.extend(changes)
        return [PushResult(c.sku, True, f"dry run: would change {c.current_price:.2f} -> {c.new_price:.2f}")
                for c in changes]


def read_changes(path: str | Path) -> list[PriceChange]:
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        return [PriceChange(r["sku"], r["name"], float(r["current_price"]), float(r["new_price"]))
                for r in csv.DictReader(handle)]
