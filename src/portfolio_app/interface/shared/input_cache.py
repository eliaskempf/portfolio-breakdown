"""Session-local parsed files, invalidated by file identity and modification.

    Cached values are copied so downstream analysis cannot change saved inputs.
"""
from copy import deepcopy
from pathlib import Path

import streamlit as st
import yaml

from portfolio_app.application import PortfolioInputs
from portfolio_app.portfolio.allocation import load_allocation
from portfolio_app.funds.etf import load_funds
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.exposure.taxonomy import load_classifications


def file_stamp(path):
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return (str(path.resolve()), None)


def cached_input(key, signature, loader, *, immutable=False):
    cache = st.session_state.setdefault('parsed_inputs', {})
    previous = cache.get(key)
    if previous is None or previous[0] != signature:
        previous = (signature, loader())
        cache[key] = previous
    return previous[1] if immutable else deepcopy(previous[1])


def monitoring_document(directory):
    """Shared validated snapshot for read-only UI consumers of this workspace."""
    from portfolio_app.monitoring.monitoring_store import FILE, load_monitoring
    return cached_input('monitoring_document', file_stamp(directory / FILE),
                        lambda: load_monitoring(directory)[0], immutable=True)


def observation_snapshot(directory):
    from portfolio_app.performance.history_observations import FILE, load_observations
    return cached_input('history_observations', file_stamp(directory / FILE),
                        lambda: load_observations(directory), immutable=True)


def load_inputs(directory):
    directory = Path(directory)
    if any((directory / name).exists() for name in ('.allocation-publication.json', '.history-publication.json')):
        from portfolio_app.workspace.workspace_lock import workspace_lock
        with workspace_lock(directory):
            pass
    holdings_path = directory / 'holdings.csv'
    from portfolio_app.ledger.account_journal import load_journal
    from portfolio_app.ledger.security_history import effective_holdings
    from portfolio_app.portfolio.positions import HoldingsSnapshot
    def effective_snapshot():
        snapshot = read_snapshot(holdings_path)
        journal, revision = load_journal(directory)
        if journal.securities:
            snapshot = HoldingsSnapshot(effective_holdings(snapshot.holdings, journal.securities), snapshot.revision)
        snapshot.holdings.attrs['history_journal_revision'] = revision
        return snapshot
    journal_stamp = file_stamp(directory / 'account-journal.json')
    snapshot = cached_input('holdings', (file_stamp(holdings_path), journal_stamp), effective_snapshot)
    path = directory / 'allocation.yaml'
    allocation = cached_input('allocation', (file_stamp(path), snapshot.revision, journal_stamp), lambda: load_allocation(path, snapshot.holdings))
    path = directory / 'classifications.yaml'
    classifications = cached_input('classifications', file_stamp(path), lambda: load_classifications(path) if path.exists() else {})
    signature = fund_sources(directory)
    funds = cached_input('funds', signature, lambda: load_funds(directory / 'etfs'))
    return PortfolioInputs(snapshot, allocation, classifications, funds)


def fund_sources(directory):
    manifests = sorted((directory / 'etfs').glob('*.yaml'))
    manifest_stamp = tuple(file_stamp(p) for p in manifests)
    def holdings_files():
        files = []
        for manifest in manifests:
            raw = yaml.safe_load(manifest.read_text())
            if isinstance(raw, dict) and isinstance(raw.get('holdings_file'), str):
                files.append(manifest.parent / raw['holdings_file'])
                if raw.get('basket_file'):
                    files.append(manifest.parent / raw['basket_file'])
        return files
    try:
        files = cached_input('fund_paths', manifest_stamp, holdings_files)
    except (OSError, ValueError, yaml.YAMLError):
        # Use the canonical loader's validation/error reporting.
        files = []
    return str(directory.resolve()), manifest_stamp, tuple(file_stamp(p) for p in files)
