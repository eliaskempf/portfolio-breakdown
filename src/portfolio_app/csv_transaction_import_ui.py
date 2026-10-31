"""CSV extraction and mapping feeding the ordinary transaction history."""
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.csv_transaction_imports import event_for, prepare_import
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.import_state import review_control, clear_transaction_draft
from portfolio_app.positions import read_snapshot
from portfolio_app.transaction_csv import SIMPLE_FIELDS, TransactionRow, digest, read_csv
from portfolio_app.transaction_import_commands import positions_content, save_transaction_import


def control(widget, *args, key, **kwargs):
    return review_control(widget, *args, key='import_tx_'+key, **kwargs)


def _rows(journal):
    mode = control(st.radio, 'Transactions', ['Upload CSV', 'Saved imports'], horizontal=True,
                   key='input') if journal.transaction_imports else 'Upload CSV'
    if mode == 'Saved imports':
        sources = list(dict.fromkeys(r.facts['source_id'] for r in journal.transaction_imports))
        labels = {}
        for number, source in enumerate(sources, 1):
            dates = sorted(r.facts.get('effective_date') or r.facts.get('timestamp_utc', '')[:10]
                           for r in journal.transaction_imports if r.facts['source_id'] == source)
            dates = [value for value in dates if value]
            labels[source] = f'Saved transactions {number}' + (f' · {dates[0]} – {dates[-1]}' if dates else '')
        source = control(st.selectbox, 'Import', sources, format_func=labels.get, filter_mode=None, key='saved_source')
        st.button('Discard review', on_click=clear_transaction_draft, key='import_tx_discard',
                  help='Clear this review and return to upload. Saved transactions are retained.')
        replaced = {r.replaces for r in journal.transaction_imports if r.replaces}
        return tuple(TransactionRow(r.raw, r.facts, r.issues) for r in journal.transaction_imports
                     if r.facts['source_id'] == source and r.id not in replaced)
    upload = st.file_uploader('Transaction CSV', type=['csv'], key='import_tx_upload')
    if upload:
        st.session_state['import_tx_content'] = (upload.name, upload.getvalue())
    content = st.session_state.get('import_tx_content')
    if content:
        st.caption(content[0])
        st.button('Discard review', on_click=clear_transaction_draft, key='import_tx_discard',
                  help='Clear the uploaded file and unsaved review choices. Changing tabs keeps the review.')
        return read_csv(content[1])
    st.download_button('Download CSV template', ','.join(SIMPLE_FIELDS)+'\n',
                       file_name='transactions.csv', mime='text/csv')
    return ()


def render_transaction_import(directory, *, demo=False):
    directory = Path(directory)
    try:
        if draft := st.session_state.get('import_tx_review'):
            _review_import(directory, draft)
            return
        st.subheader('Import transactions')
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        rows = _rows(journal)
        if not rows:
            return
        if len({r.facts['source_id'] for r in rows}) != 1:
            raise DataError('Review one source ledger at a time.')
        token = digest([r.identity for r in rows])[:16]+'_'
        from portfolio_app.history_observations import load_observations
        from portfolio_app.portfolio_settings import load_settings
        observations, observation_revision = load_observations(directory)
        target = load_settings(directory).reporting_currency
        baseline = revision, snapshot.revision, observation_revision, target
        baseline_key = 'import_tx_baseline_'+token
        if st.session_state.setdefault(baseline_key, baseline) != baseline:
            st.warning('Portfolio changed. Reload this review before applying.')
            if st.button('Reload transaction review'):
                st.session_state.pop(baseline_key, None)
                st.rerun()
            return
        from portfolio_app.transaction_mapping_ui import render_mapping
        mapping, additions, listing_changes = render_mapping(directory, snapshot, journal, rows, token, currency=target, demo=demo)
        content = positions_content((directory/'holdings.csv').read_text(encoding='utf-8-sig')
                                    if (directory/'holdings.csv').exists() else '', snapshot, additions, listing_changes=listing_changes)
        parse_holdings(content)  # Validate proposed metadata before entering review.
        latest = {r.facts['source_id']: {} for r in journal.transaction_imports}
        for receipt in journal.transaction_imports:
            latest[receipt.facts['source_id']][receipt.facts['event_id']] = receipt
        retained = {r.id for r in journal.transaction_imports}
        conflicts = [r for r in rows if r.key[1] in latest.get(r.key[0], {}) and r.identity not in retained]
        corrections = []
        if conflicts:
            st.caption(f'{len(conflicts)} source records have changed.')
            if control(st.checkbox, 'Use changed records as corrections', key=token+'corrections'):
                corrections = [r.identity for r in conflicts]
        valid, errors = [], []
        for row in rows:
            try:
                valid.append((row, event_for(row, mapping)))
            except (DataError, ValueError) as exc:
                problem = 'Choose a holding for every asset used by this transaction.' if str(exc).startswith('Map the custody position') else str(exc)
                errors.append({'Date': row.date or 'Unknown', 'Asset': row.facts['asset_id'], 'Problem': problem})
        from portfolio_app.transaction_review_ui import render_transactions
        active = {i for b in journal.securities for i in b.active_ids}
        if valid:
            render_transactions(valid, active, retained, token)
        if errors:
            st.dataframe(pd.DataFrame(errors), hide_index=True, width='stretch')
        # Keep the ordinary path short; row selection and ordering are exceptional.
        selected = [r.identity for r, _ in valid]
        advanced = st.expander('Import options')
        with advanced:
            from portfolio_app.history_ui import _event_label
            labels = {r.identity: f'{number}. {r.facts["asset_id"]} · {_event_label(event)}'
                      for number, (r, event) in enumerate(valid, 1)}
            selected = control(st.multiselect, 'Included transactions', selected, default=selected,
                format_func=labels.get, key=token+'selected')
        positions = set(mapping.values())
        books = {b.position_id: b for b in journal.securities}
        from portfolio_app.security_history import selected_events
        supplied_starts = {mapping[r.facts['account_id'], r.facts['asset_id']] for r in rows
            if r.identity in selected and r.facts['event_type'] in ('opening', 'checkpoint')}
        needs_start = [p for p in sorted(positions) if p not in supplied_starts and
            (p not in books or not any(e.kind in ('opening', 'checkpoint') for e in selected_events(books[p])))]
        zero = False
        if needs_start:
            with advanced:
                start = control(st.selectbox, 'Before the first transaction', ['Zero holdings', 'Earlier holdings'],
                    key=token+'start', help='A history from the first acquisition starts at zero. A later export needs a recorded opening balance; current holdings are only the ending comparison.')
                zero = start == 'Zero holdings'
        if needs_start and not zero:
            st.info('Starting balance required')
        # Collapse multiple owned locations only for genuinely unmapped legacy
        # files; existing bindings and linked cash keep their reviewed semantics.
        aggregate = tuple(sorted({a for a, _ in mapping})) if len({a for a, _ in mapping}) > 1 and not any(b.custody_id for b in books.values() if b.position_id in positions) else ()
        options = dict(selected_ids=selected, starts_at_zero=zero, allow_incomplete=True,
                       correction_ids=corrections, aggregate_accounts=aggregate)
        actions = st.container(horizontal=True)
        if actions.button('Review against portfolio', type='primary',
                          disabled=bool(errors or not selected or conflicts and not corrections or needs_start and not zero), key='import_tx_review_start'):
            st.session_state['import_tx_review'] = dict(rows=rows, mapping=mapping, additions=additions,
                listing_changes=listing_changes, options=options, token=token, baseline=baseline,
                needs_start=needs_start, zero=zero)
            st.rerun()
        if actions.button('Save evidence only', disabled=bool(conflicts and not corrections), key='import_tx_save_evidence'):
            existing_mapping = {pair: p for pair, p in mapping.items() if p in set(snapshot.holdings.position_id)}
            result = save_transaction_import(directory, rows, existing_mapping,
                expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
                correction_ids=corrections)
            st.session_state.pop(baseline_key, None)
            st.session_state['import_tx_notice'] = f'{result.added} records saved'
            st.rerun()
        if notice := st.session_state.get('import_tx_notice'):
            st.caption(notice)
    except (DataError, ValueError, UnicodeError, OSError) as exc:
        st.warning(str(exc))


def _return_to_import():
    st.session_state.pop('import_tx_review', None)


def _review_import(directory, draft):
    from portfolio_app.history_observations import load_observations, ObservationFX
    from portfolio_app.portfolio_settings import load_settings
    from portfolio_app.history_review_ui import ReviewPreview, render_history_review
    journal, revision = load_journal(directory)
    snapshot = read_snapshot(directory/'holdings.csv')
    observations, observation_revision = load_observations(directory)
    target = load_settings(directory).reporting_currency
    baseline = revision, snapshot.revision, observation_revision, target
    if draft['baseline'] != baseline:
        st.warning('Portfolio changed. Reload this review before applying.')
        def reload():
            st.session_state.pop('import_tx_baseline_'+draft['token'], None)
            _return_to_import()
        st.button('Reload transaction review', on_click=reload)
        return
    st.subheader('Review against portfolio')
    content = positions_content((directory/'holdings.csv').read_text(encoding='utf-8-sig')
        if (directory/'holdings.csv').exists() else '', snapshot, draft['additions'], listing_changes=draft['listing_changes'])
    holdings = parse_holdings(content)
    active = {i for b in journal.securities for i in b.active_ids}
    def options(excluded):
        return {**draft['options'], 'selected_ids':[i for i in draft['options']['selected_ids'] if i not in excluded],
                'accept_duplicates':True}
    def build(excluded):
        preview = prepare_import(journal, holdings, draft['rows'], draft['mapping'], target=target,
            historical=ObservationFX(observations), **options(excluded))
        note = ''
        if draft['zero'] and preview.applied:
            names = holdings.set_index('position_id')['name']
            note = 'Starts at zero · '+', '.join(names.loc[draft['needs_start']])
        return ReviewPreview(preview.journal, preview.comparisons if preview.applied else (),
            cash=preview.cash_changes, matches=preview.matches,
            editable=frozenset(draft['options']['selected_ids'])-active,
            known_positions=frozenset(snapshot.holdings.position_id),
            checkpoints=preview.checkpoints, warnings=preview.warnings, starting_note=note)
    def apply(excluded):
        save_transaction_import(directory, draft['rows'], draft['mapping'], expected_journal_revision=revision,
            expected_holdings_revision=snapshot.revision, additions=draft['additions'], listing_changes=draft['listing_changes'],
            accept_reconciliation=True, accept_checkpoints=True, comparison_date=date.today().isoformat(),
            expected_observations_revision=observation_revision,
            expected_reporting_currency=target, **options(excluded))
        st.session_state['import_tx_pending_complete'] = True
        _return_to_import()
        st.rerun()
    render_history_review(build, key='import_tx_'+draft['token']+'review_', apply=apply, keep=_return_to_import, keep_label='Back to import')
