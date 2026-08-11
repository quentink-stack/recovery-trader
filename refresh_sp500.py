"""Refresh the local S&P 500 constituent list used by the screener."""

from __future__ import annotations

import csv
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.request import Request, urlopen


SOURCE_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
DESTINATION = Path(__file__).parent / "data" / "sp500.csv"


class ConstituentsTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_table = False
        self.in_row = False
        self.in_cell = False
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.cell: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "table" and attributes.get("id") == "constituents":
            self.in_table = True
        elif self.in_table and tag == "tr":
            self.in_row = True
            self.row = []
        elif self.in_row and tag in {"th", "td"}:
            self.in_cell = True
            self.cell = []

    def handle_data(self, data: str) -> None:
        if self.in_cell:
            self.cell.append(data)

    def handle_endtag(self, tag: str) -> None:
        if self.in_cell and tag in {"th", "td"}:
            self.row.append("".join(self.cell).strip())
            self.in_cell = False
        elif self.in_table and tag == "tr":
            if self.row:
                self.rows.append(self.row)
            self.in_row = False
        elif self.in_table and tag == "table":
            self.in_table = False


def fetch_constituents() -> list[tuple[str, str]]:
    request = Request(SOURCE_URL, headers={"User-Agent": "recovery-trader/1.0 (local research tool)"})
    with urlopen(request, timeout=30) as response:
        document = response.read().decode("utf-8")
    parser = ConstituentsTableParser()
    parser.feed(document)
    rows = parser.rows[1:]
    constituents = [(unescape(row[0]).strip().upper(), unescape(row[1]).strip()) for row in rows if len(row) >= 2]
    if len(constituents) < 500:
        raise ValueError(f"Expected at least 500 constituents, found {len(constituents)}. The source table may have changed.")
    return constituents


def main() -> None:
    constituents = fetch_constituents()
    temporary_path = DESTINATION.with_suffix(".tmp")
    with temporary_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ticker", "company"])
        writer.writerows(constituents)
    temporary_path.replace(DESTINATION)
    print(f"Wrote {len(constituents)} S&P 500 constituents to {DESTINATION}")


if __name__ == "__main__":
    main()
