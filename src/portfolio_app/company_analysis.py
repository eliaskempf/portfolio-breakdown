"""Prepare company analytics without rendering or fetching market data."""
from dataclasses import dataclass

from portfolio_app.company_merges import build_plan, load_company_names, load_settings
from portfolio_app.stock_exposure import StockExposure, load_company_identities, stock_exposure


@dataclass(frozen=True)
class CompanyAnalysis:
    exposure: StockExposure
    proxy_notes: tuple[str, ...]


def prepare_company_analysis(valued, snapshots, funds, identities, merge_settings, company_names):
    typed = valued.copy()
    typed['instrument_type'] = [snapshots[row.id].kind for row in typed.itertuples()]
    plan = build_plan(typed, funds, identities, merge_settings, {}, company_names)
    positions, linked_funds, _ = plan.apply(typed, funds, {})
    return CompanyAnalysis(stock_exposure(positions, linked_funds, identities=identities),
                           tuple(f'{fund.name}: {fund.proxy_source}' for fund in linked_funds if fund.proxy_source))


def load_company_analysis(valued, snapshots, funds, directory):
    return prepare_company_analysis(valued, snapshots, funds,
        load_company_identities(directory / 'company-identities.yaml'),
        load_settings(directory / 'company-merges.yaml'),
        load_company_names(directory / 'company-names.yaml'))
