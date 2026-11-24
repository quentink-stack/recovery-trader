"""No-key ALFRED reader for point-in-time financial-conditions data."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile


ALFRED_NFCI_DOWNLOAD_URL = "https://alfred.stlouisfed.org/series/downloaddata?seid=NFCI"
MAX_DOWNLOAD_BYTES = 8_000_000


class FredDataError(RuntimeError):
    """Raised when an official FRED/ALFRED response cannot be used safely."""


@dataclass(frozen=True)
class VintageObservation:
    """One NFCI value and the dates on which that revision was public."""

    observation_date: date
    value: float
    realtime_start: date
    realtime_end: date | None


class _VintageDateParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_vintage_select = False
        self.dates: list[date] = []
        self.observation_start: date | None = None
        self.observation_end: date | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = dict(attrs)
        if tag == "select" and attributes.get("id") == "form_selected_vintage_dates":
            self.in_vintage_select = True
        elif tag == "input" and attributes.get("id") in {"form_obs_start_date", "form_obs_end_date"}:
            raw_value = attributes.get("value")
            if raw_value:
                try:
                    parsed = date.fromisoformat(raw_value)
                except ValueError:
                    return
                if attributes["id"] == "form_obs_start_date":
                    self.observation_start = parsed
                else:
                    self.observation_end = parsed
        elif tag == "option" and self.in_vintage_select:
            raw_value = attributes.get("value")
            if raw_value:
                try:
                    self.dates.append(date.fromisoformat(raw_value))
                except ValueError:
                    pass

    def handle_endtag(self, tag: str) -> None:
        if tag == "select" and self.in_vintage_select:
            self.in_vintage_select = False


def parse_vintage_dates(html: str) -> tuple[date, ...]:
    """Extract the official NFCI release/vintage dates from the ALFRED form."""
    parser = _VintageDateParser()
    parser.feed(html)
    return tuple(sorted(set(parser.dates)))


def parse_observation_bounds(html: str) -> tuple[date, date]:
    """Return the observation range currently accepted by the ALFRED form."""
    parser = _VintageDateParser()
    parser.feed(html)
    if parser.observation_start is None or parser.observation_end is None:
        raise FredDataError("ALFRED download form did not contain observation bounds.")
    return parser.observation_start, parser.observation_end


def parse_realtime_period_zip(payload: bytes) -> tuple[VintageObservation, ...]:
    """Parse ALFRED's zipped CSV real-time-period export."""
    try:
        with ZipFile(io.BytesIO(payload)) as archive:
            csv_names = [name for name in archive.namelist() if name.lower().endswith(".csv")]
            if len(csv_names) != 1:
                raise FredDataError("ALFRED response did not contain exactly one observation CSV.")
            text = archive.read(csv_names[0]).decode("utf-8-sig")
    except (BadZipFile, KeyError, UnicodeDecodeError) as exc:
        raise FredDataError("ALFRED returned an unreadable NFCI archive.") from exc

    observations: list[VintageObservation] = []
    reader = csv.DictReader(io.StringIO(text))
    required = {"period_start_date", "NFCI", "realtime_start_date", "realtime_end_date"}
    if not required.issubset(reader.fieldnames or ()):
        raise FredDataError("ALFRED NFCI CSV did not contain the expected real-time-period fields.")
    try:
        for row in reader:
            raw_end = (row.get("realtime_end_date") or "").strip()
            observations.append(
                VintageObservation(
                    observation_date=date.fromisoformat(row["period_start_date"]),
                    value=float(row["NFCI"]),
                    realtime_start=date.fromisoformat(row["realtime_start_date"]),
                    realtime_end=(
                        None if raw_end in {"", ".", "#NA"} else date.fromisoformat(raw_end)
                    ),
                )
            )
    except (KeyError, TypeError, ValueError) as exc:
        raise FredDataError("ALFRED NFCI CSV contained an invalid observation.") from exc
    if not observations:
        raise FredDataError("ALFRED returned no NFCI observations for the requested dates.")
    return tuple(sorted(observations, key=lambda item: (item.observation_date, item.realtime_start)))


class AlfredClient:
    """Download NFCI revisions from ALFRED without requiring an API key."""

    def __init__(self, timeout: int = 45) -> None:
        if timeout < 1:
            raise ValueError("ALFRED timeout must be positive.")
        self.timeout = timeout
        self.headers = {
            "Accept": "text/html,application/zip,text/csv;q=0.9,*/*;q=0.8",
            "User-Agent": "Recovery-Trader/1.0 point-in-time research",
        }

    def nfci_realtime_periods(
        self,
        observation_start: date,
        observation_end: date,
        vintage_start: date,
        vintage_end: date,
    ) -> tuple[VintageObservation, ...]:
        """Return all NFCI revisions overlapping the requested vintage range."""
        if observation_start > observation_end:
            raise ValueError("NFCI observation start must not follow its end.")
        if vintage_start > vintage_end:
            raise ValueError("NFCI vintage start must not follow its end.")

        form_html = self._request(Request(ALFRED_NFCI_DOWNLOAD_URL, headers=self.headers)).decode(
            "utf-8", "replace"
        )
        available_observation_start, available_observation_end = parse_observation_bounds(form_html)
        effective_observation_start = max(observation_start, available_observation_start)
        effective_observation_end = min(observation_end, available_observation_end)
        if effective_observation_start > effective_observation_end:
            raise FredDataError("ALFRED has no NFCI observations in the requested range.")
        vintage_dates = tuple(
            item for item in parse_vintage_dates(form_html) if vintage_start <= item <= vintage_end
        )
        if not vintage_dates:
            raise FredDataError("ALFRED has no NFCI vintage dates in the requested range.")

        fields: list[tuple[str, str]] = [
            ("form[units]", "lin"),
            ("form[obs_start_date]", effective_observation_start.isoformat()),
            ("form[obs_end_date]", effective_observation_end.isoformat()),
            *(('form[selected_vintage_dates][]', item.isoformat()) for item in vintage_dates),
            ("form[entered_vintage_dates]", ""),
            ("form[file_type]", "1"),
            ("form[file_format]", "csv"),
            ("form[download_data]", ""),
        ]
        request = Request(
            ALFRED_NFCI_DOWNLOAD_URL,
            data=urlencode(fields).encode("ascii"),
            headers=self.headers,
        )
        return parse_realtime_period_zip(self._request(request))

    def _request(self, request: Request) -> bytes:
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = response.read(MAX_DOWNLOAD_BYTES + 1)
        except (HTTPError, URLError, TimeoutError) as exc:
            raise FredDataError(f"Could not download NFCI data from ALFRED: {exc}") from exc
        if len(payload) > MAX_DOWNLOAD_BYTES:
            raise FredDataError("ALFRED NFCI response exceeded the download limit.")
        return payload


def observations_to_rows(observations: Iterable[VintageObservation]) -> list[dict[str, str | float | None]]:
    """Return stable CSV-ready rows for an ignored experiment snapshot."""
    return [
        {
            "period_start_date": item.observation_date.isoformat(),
            "NFCI": item.value,
            "realtime_start_date": item.realtime_start.isoformat(),
            "realtime_end_date": item.realtime_end.isoformat() if item.realtime_end else None,
        }
        for item in observations
    ]
