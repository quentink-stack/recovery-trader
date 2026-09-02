"""Read-only SEC EDGAR client for ticker, filing, and XBRL earnings evidence."""

from __future__ import annotations

import json
import os
import re
from configparser import ConfigParser
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Iterable
from urllib.request import Request, urlopen


SEC_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SEC_SUBMISSIONS_URL = "https://data.sec.gov/submissions"
SEC_COMPANY_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts"
SEC_ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"

REVENUE_TAGS = ("RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet", "Revenues")
NET_INCOME_TAGS = ("NetIncomeLoss",)
EPS_BASIC_TAGS = ("EarningsPerShareBasic",)
EPS_DILUTED_TAGS = ("EarningsPerShareDiluted",)
CASH_TAGS = ("CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents")
OPERATING_INCOME_TAGS = ("OperatingIncomeLoss",)
OPERATING_CASH_FLOW_TAGS = ("NetCashProvidedByUsedInOperatingActivities",)
CAPEX_TAGS = (
    "PaymentsToAcquirePropertyPlantAndEquipment",
    "PaymentsToAcquireProductiveAssets",
    "PaymentsForAdditionsToPropertyPlantAndEquipment",
)
DEBT_CURRENT_TAGS = ("LongTermDebtCurrent", "ShortTermBorrowings", "ShortTermDebt")
DEBT_NONCURRENT_TAGS = ("LongTermDebtNoncurrent", "LongTermDebtAndFinanceLeaseObligationsNoncurrent")
DEBT_TOTAL_TAGS = ("LongTermDebtAndFinanceLeaseObligations", "LongTermDebtAndCapitalLeaseObligations", "LongTermDebt")
DILUTED_SHARES_TAGS = ("WeightedAverageNumberOfDilutedSharesOutstanding",)


class SecEdgarError(RuntimeError):
    """Raised when SEC EDGAR data cannot be retrieved or interpreted."""


@dataclass(frozen=True)
class SecFiling:
    cik: str
    accession_number: str
    form: str
    filing_date: date
    report_date: date | None
    primary_document: str
    primary_document_description: str
    items: tuple[str, ...]


@dataclass(frozen=True)
class EarningsRelease:
    filing: SecFiling
    exhibit_name: str
    exhibit_url: str


@dataclass(frozen=True)
class EarningsFacts:
    cik: str
    form: str
    filing_date: date
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    revenue: float | None
    net_income: float | None
    eps_basic: float | None
    eps_diluted: float | None
    cash: float | None
    accession_number: str
    operating_income: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None
    debt: float | None = None
    diluted_shares: float | None = None
    prior_year: "ComparableEarningsFacts | None" = None


@dataclass(frozen=True)
class ComparableEarningsFacts:
    """A prior-year 10-Q/10-K period aligned to an EarningsFacts record."""

    form: str
    filing_date: date
    period_start: date | None
    period_end: date
    fiscal_year: int | None
    fiscal_period: str | None
    revenue: float | None
    operating_income: float | None
    net_income: float | None
    eps_diluted: float | None
    operating_cash_flow: float | None
    capex: float | None
    debt: float | None
    cash: float | None
    diluted_shares: float | None
    accession_number: str


@dataclass(frozen=True)
class CompanyProfile:
    """Minimal SEC classification used only for deterministic metric treatment."""

    cik: str
    sic: int | None
    sic_description: str | None


@dataclass(frozen=True)
class _XbrlFact:
    tag: str
    form: str
    filed: date
    start: date | None
    end: date
    value: float
    accession_number: str
    fiscal_year: int | None
    fiscal_period: str | None


class SecEdgarClient:
    """Minimal, cacheable SEC reader. A real contact email is required by SEC policy."""

    def __init__(self, user_agent: str, timeout: int = 30) -> None:
        if "@" not in user_agent:
            raise ValueError("SEC EDGAR requires a descriptive User-Agent that includes a contact email address.")
        if timeout < 1:
            raise ValueError("SEC EDGAR timeout must be positive.")
        self.user_agent = user_agent
        self.timeout = timeout
        self._ticker_ciks: dict[str, str] | None = None
        self._submissions_by_cik: dict[str, dict[str, Any]] = {}
        self._company_facts_by_cik: dict[str, dict[str, Any]] = {}

    @classmethod
    def from_config(cls, config_path: Path | None = None) -> "SecEdgarClient":
        """Load the SEC contact identity from environment or ignored local config."""
        environment_user_agent = os.getenv("SEC_USER_AGENT", "").strip()
        environment_timeout = os.getenv("SEC_TIMEOUT", "").strip()
        if environment_user_agent:
            return cls(environment_user_agent, int(environment_timeout or "30"))

        path = config_path or Path(__file__).parents[2] / "config" / "sec_edgar.ini"
        config = ConfigParser()
        config.read(path, encoding="utf-8")
        if not config.has_section("sec_edgar"):
            raise ValueError("Create config/sec_edgar.ini from config/sec_edgar.example.ini, then add your contact email.")
        section = config["sec_edgar"]
        user_agent = section.get("user_agent", "").strip()
        if not user_agent or user_agent.startswith("PASTE_"):
            raise ValueError("Add your contact email to config/sec_edgar.ini as the SEC User-Agent.")
        return cls(user_agent, section.getint("timeout_seconds", fallback=30))

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": self.user_agent,
            },
        )
        try:
            with urlopen(request, timeout=self.timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (OSError, TimeoutError, json.JSONDecodeError) as exc:
            raise SecEdgarError(f"SEC EDGAR request failed: {exc}") from exc
        if not isinstance(payload, dict):
            raise SecEdgarError("SEC EDGAR returned an unexpected JSON payload.")
        return payload

    @staticmethod
    def _normalize_cik(cik: str | int) -> str:
        digits = str(cik).strip()
        if not digits.isdigit():
            raise ValueError("CIK must contain only digits.")
        return digits.zfill(10)

    def cik_for_ticker(self, ticker: str) -> str:
        """Return the zero-padded SEC CIK for a listed ticker."""
        normalized_ticker = ticker.strip().upper()
        if not normalized_ticker:
            raise ValueError("Ticker cannot be empty.")
        if self._ticker_ciks is None:
            payload = self._get_json(SEC_TICKERS_URL)
            self._ticker_ciks = {
                str(item.get("ticker", "")).upper(): self._normalize_cik(item["cik_str"])
                for item in payload.values()
                if isinstance(item, dict) and item.get("ticker") and item.get("cik_str") is not None
            }
        try:
            return self._ticker_ciks[normalized_ticker]
        except KeyError as exc:
            raise SecEdgarError(f"SEC EDGAR did not find a CIK for {normalized_ticker}.") from exc

    def filing_history(self, cik: str | int) -> tuple[SecFiling, ...]:
        """Return the SEC's recent filing history, newest first."""
        normalized_cik = self._normalize_cik(cik)
        payload = self._submissions(normalized_cik)
        recent = payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            raise SecEdgarError("SEC EDGAR submissions response did not contain recent filings.")

        accessions = recent.get("accessionNumber", [])
        if not isinstance(accessions, list):
            raise SecEdgarError("SEC EDGAR submissions response had invalid accession numbers.")

        filings: list[SecFiling] = []
        for index, accession in enumerate(accessions):
            if not isinstance(accession, str):
                continue
            filing_date = _parse_date(_at(recent, "filingDate", index))
            form = _at(recent, "form", index)
            if filing_date is None or not isinstance(form, str):
                continue
            items = _at(recent, "items", index)
            filings.append(
                SecFiling(
                    normalized_cik,
                    accession,
                    form,
                    filing_date,
                    _parse_date(_at(recent, "reportDate", index)),
                    str(_at(recent, "primaryDocument", index) or ""),
                    str(_at(recent, "primaryDocDescription", index) or ""),
                    tuple(part.strip() for part in items.split(",")) if isinstance(items, str) and items.strip() else (),
                )
            )
        return tuple(filings)

    def company_profile(self, cik: str | int) -> CompanyProfile:
        """Return the SEC SIC classification without relying on a vendor sector map."""
        normalized_cik = self._normalize_cik(cik)
        payload = self._submissions(normalized_cik)
        sic = payload.get("sic")
        return CompanyProfile(
            normalized_cik,
            sic if isinstance(sic, int) else int(sic) if isinstance(sic, str) and sic.isdigit() else None,
            payload.get("sicDescription") if isinstance(payload.get("sicDescription"), str) else None,
        )

    def _submissions(self, normalized_cik: str) -> dict[str, Any]:
        if normalized_cik not in self._submissions_by_cik:
            self._submissions_by_cik[normalized_cik] = self._get_json(
                f"{SEC_SUBMISSIONS_URL}/CIK{normalized_cik}.json"
            )
        return self._submissions_by_cik[normalized_cik]

    def latest_earnings_release(self, cik: str | int, filings: Iterable[SecFiling] | None = None) -> EarningsRelease | None:
        """Find the newest Item 2.02 8-K that includes an EX-99.1 exhibit.

        Only each filing's lightweight directory index is fetched; exhibit HTML
        is deliberately not downloaded at this stage.
        """
        normalized_cik = self._normalize_cik(cik)
        candidate_filings = filings if filings is not None else self.filing_history(normalized_cik)
        for filing in candidate_filings:
            if filing.form != "8-K" or "2.02" not in filing.items:
                continue
            archive_cik = str(int(normalized_cik))
            accession_path = filing.accession_number.replace("-", "")
            index_url = f"{SEC_ARCHIVES_URL}/{archive_cik}/{accession_path}/index.json"
            index_payload = self._get_json(index_url)
            directory = index_payload.get("directory", {})
            items = directory.get("item", []) if isinstance(directory, dict) else []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    continue
                exhibit_name = item.get("name")
                if not isinstance(exhibit_name, str) or not _is_exhibit_99_1(item, exhibit_name):
                    continue
                return EarningsRelease(filing, exhibit_name, f"{SEC_ARCHIVES_URL}/{archive_cik}/{accession_path}/{exhibit_name}")
        return None

    def latest_earnings_facts(self, cik: str | int) -> EarningsFacts | None:
        """Return normalized facts from the newest available 10-Q or 10-K."""
        normalized_cik = self._normalize_cik(cik)
        return _earnings_facts_from_all(_collect_facts(self._company_facts(normalized_cik)), normalized_cik)

    def earnings_facts_as_of(self, cik: str | int, availability_date: date) -> EarningsFacts | None:
        """Return only SEC facts that were public by an earnings availability date."""
        normalized_cik = self._normalize_cik(cik)
        return _earnings_facts_from_all(
            _collect_facts(self._company_facts(normalized_cik)),
            normalized_cik,
            availability_date,
        )

    def _company_facts(self, normalized_cik: str) -> dict[str, Any]:
        if normalized_cik not in self._company_facts_by_cik:
            self._company_facts_by_cik[normalized_cik] = self._get_json(
                f"{SEC_COMPANY_FACTS_URL}/CIK{normalized_cik}.json"
            )
        return self._company_facts_by_cik[normalized_cik]


def _earnings_facts_from_all(
    all_facts: tuple[_XbrlFact, ...],
    normalized_cik: str,
    availability_date: date | None = None,
) -> EarningsFacts | None:
    anchors = _facts_for_tags(all_facts, REVENUE_TAGS + NET_INCOME_TAGS + EPS_BASIC_TAGS + EPS_DILUTED_TAGS + CASH_TAGS)
    if availability_date is not None:
        anchors = tuple(anchor for anchor in anchors if anchor.filed <= availability_date)
    if not anchors:
        return None
    anchor = max(anchors, key=lambda fact: (fact.filed, fact.end, fact.accession_number))
    prior_year_anchor = _prior_year_anchor(anchors, anchor)
    return EarningsFacts(
        normalized_cik,
        anchor.form,
        anchor.filed,
        anchor.start,
        anchor.end,
        anchor.fiscal_year,
        anchor.fiscal_period,
        _matched_fact_value(all_facts, REVENUE_TAGS, anchor),
        _matched_fact_value(all_facts, NET_INCOME_TAGS, anchor),
        _matched_fact_value(all_facts, EPS_BASIC_TAGS, anchor),
        _matched_fact_value(all_facts, EPS_DILUTED_TAGS, anchor),
        _matched_fact_value(all_facts, CASH_TAGS, anchor),
        anchor.accession_number,
        _matched_fact_value(all_facts, OPERATING_INCOME_TAGS, anchor),
        _matched_fact_value(all_facts, OPERATING_CASH_FLOW_TAGS, anchor),
        _matched_fact_value(all_facts, CAPEX_TAGS, anchor),
        _matched_debt_value(all_facts, anchor),
        _matched_fact_value(all_facts, DILUTED_SHARES_TAGS, anchor),
        _comparable_facts(all_facts, prior_year_anchor),
    )


def _at(values: dict[str, Any], key: str, index: int) -> Any:
    items = values.get(key, [])
    return items[index] if isinstance(items, list) and index < len(items) else None


def _parse_date(value: Any) -> date | None:
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _collect_facts(payload: dict[str, Any]) -> tuple[_XbrlFact, ...]:
    taxonomy = payload.get("facts", {}).get("us-gaap", {})
    if not isinstance(taxonomy, dict):
        raise SecEdgarError("SEC EDGAR company facts response did not contain us-gaap facts.")
    facts: list[_XbrlFact] = []
    for tag, concept in taxonomy.items():
        units = concept.get("units", {}) if isinstance(concept, dict) else {}
        if not isinstance(units, dict):
            continue
        for entries in units.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("form") not in {"10-Q", "10-K"}:
                    continue
                filed, end = _parse_date(entry.get("filed")), _parse_date(entry.get("end"))
                value = entry.get("val")
                if filed is None or end is None or not isinstance(value, (int, float)):
                    continue
                fiscal_year = entry.get("fy")
                facts.append(
                    _XbrlFact(
                        str(tag),
                        str(entry["form"]),
                        filed,
                        _parse_date(entry.get("start")),
                        end,
                        float(value),
                        str(entry.get("accn", "")),
                        fiscal_year if isinstance(fiscal_year, int) else None,
                        entry.get("fp") if isinstance(entry.get("fp"), str) else None,
                    )
                )
    return tuple(facts)


def _facts_for_tags(facts: Iterable[_XbrlFact], tags: tuple[str, ...]) -> tuple[_XbrlFact, ...]:
    allowed_tags = set(tags)
    return tuple(fact for fact in facts if fact.tag in allowed_tags)


def _matched_fact_value(facts: Iterable[_XbrlFact], tags: tuple[str, ...], anchor: _XbrlFact) -> float | None:
    candidates = [
        fact
        for fact in _facts_for_tags(facts, tags)
        if fact.accession_number == anchor.accession_number and fact.end == anchor.end
    ]
    if not candidates:
        return None
    # Prefer an exact reporting-period match, then the shortest duration. This
    # avoids selecting a year-to-date value when a same-quarter value exists.
    candidates.sort(
        key=lambda fact: (
            fact.start != anchor.start,
            (fact.end - fact.start).days if fact.start else 10_000,
        )
    )
    return candidates[0].value


def _matched_debt_value(facts: Iterable[_XbrlFact], anchor: _XbrlFact) -> float | None:
    """Prefer current + noncurrent debt components, then use a reported-total tag."""
    current = _matched_fact_value(facts, DEBT_CURRENT_TAGS, anchor)
    noncurrent = _matched_fact_value(facts, DEBT_NONCURRENT_TAGS, anchor)
    if current is not None and noncurrent is not None:
        return current + noncurrent
    return _matched_fact_value(facts, DEBT_TOTAL_TAGS, anchor)


def _prior_year_anchor(anchors: Iterable[_XbrlFact], current: _XbrlFact) -> _XbrlFact | None:
    """Choose a same-form, same-fiscal-period prior-year fact for comparison."""
    candidates_by_date = [
        fact
        for fact in anchors
        if fact.form == current.form
        and fact.filed <= current.filed
        and 330 <= (current.end - fact.end).days <= 400
    ]
    if current.fiscal_year is None or not current.fiscal_period:
        return max(candidates_by_date, key=lambda fact: (fact.filed, fact.end, fact.accession_number), default=None)
    candidates = [
        fact
        for fact in anchors
        if fact.form == current.form
        and fact.fiscal_year == current.fiscal_year - 1
        and fact.fiscal_period == current.fiscal_period
    ]
    if candidates:
        return max(candidates, key=lambda fact: (fact.filed, fact.end, fact.accession_number))
    # A current filing can include its prior-year comparative period while both
    # facts retain the current filing's FY field. Fall back to an approximately
    # one-year period-end gap, still requiring the same form and public date.
    return max(candidates_by_date, key=lambda fact: (fact.filed, fact.end, fact.accession_number), default=None)


def _comparable_facts(facts: Iterable[_XbrlFact], anchor: _XbrlFact | None) -> ComparableEarningsFacts | None:
    if anchor is None:
        return None
    return ComparableEarningsFacts(
        anchor.form,
        anchor.filed,
        anchor.start,
        anchor.end,
        anchor.fiscal_year,
        anchor.fiscal_period,
        _matched_fact_value(facts, REVENUE_TAGS, anchor),
        _matched_fact_value(facts, OPERATING_INCOME_TAGS, anchor),
        _matched_fact_value(facts, NET_INCOME_TAGS, anchor),
        _matched_fact_value(facts, EPS_DILUTED_TAGS, anchor),
        _matched_fact_value(facts, OPERATING_CASH_FLOW_TAGS, anchor),
        _matched_fact_value(facts, CAPEX_TAGS, anchor),
        _matched_debt_value(facts, anchor),
        _matched_fact_value(facts, CASH_TAGS, anchor),
        _matched_fact_value(facts, DILUTED_SHARES_TAGS, anchor),
        anchor.accession_number,
    )


def _is_exhibit_99_1(item: dict[str, Any], filename: str) -> bool:
    """Identify EX-99.1 when the SEC directory index omits document types."""
    if str(item.get("type", "")).upper() == "EX-99.1":
        return True
    return re.search(r"(?:exhibit|ex)[-_]?99[-_]?1", filename.lower()) is not None
