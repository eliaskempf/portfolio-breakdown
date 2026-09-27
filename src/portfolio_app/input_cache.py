"""Session-local parsed files, invalidated by file identity and modification.

    Cached values are copied so downstream analysis cannot change saved inputs.
"""
from copy import deepcopy
from pathlib import Path

import streamlit as st
import yaml

from portfolio_app.allocation import load_allocation
from portfolio_app.etf import load_funds
from portfolio_app.positions import read_snapshot
from portfolio_app.taxonomy import load_classifications


def file_stamp(path):
    try:
        stat = path.stat()
        return (str(path.resolve()), stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    except FileNotFoundError:
        return (str(path.resolve()), None)


def cached_input(key, signature, loader):
    cache = st.session_state.setdefault('parsed_inputs', {})
    previous = cache.get(key)
    if previous is None or previous[0] != signature:
        previous = (signature, loader())
        cache[key] = previous
    return deepcopy(previous[1])


def load_inputs(directory):
    directory = Path(directory)
    holdings_path = directory / 'holdings.csv'
    snapshot = cached_input('holdings', file_stamp(holdings_path), lambda: read_snapshot(holdings_path))
    path = directory / 'allocation.yaml'
    allocation = cached_input('allocation', (file_stamp(path), snapshot.revision), lambda: load_allocation(path, snapshot.holdings))
    path = directory / 'classifications.yaml'
    classifications = cached_input('classifications', file_stamp(path), lambda: load_classifications(path) if path.exists() else {})
    manifests = sorted((directory / 'etfs').glob('*.yaml'))
    manifest_stamp = tuple(file_stamp(p) for p in manifests)
    def holdings_files():
        files = []
        for manifest in manifests:
            raw = yaml.safe_load(manifest.read_text())
            if isinstance(raw, dict) and isinstance(raw.get('holdings_file'), str):
                files.append(manifest.parent / raw['holdings_file'])
        return files
    try:
        files = cached_input('fund_paths', manifest_stamp, holdings_files)
    except (OSError, ValueError, yaml.YAMLError):
        # Use the canonical loader's validation/error reporting.
        files = []
    signature = (str(directory.resolve()), manifest_stamp, tuple(file_stamp(p) for p in files))
    funds = cached_input('funds', signature, lambda: load_funds(directory / 'etfs'))
    return snapshot, allocation, classifications, funds
