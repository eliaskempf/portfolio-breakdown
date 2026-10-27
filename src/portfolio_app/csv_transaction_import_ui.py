"""CSV extraction and mapping feeding the ordinary transaction history."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.csv_transaction_imports import event_for, prepare_import
from portfolio_app.holdings import DataError, parse_holdings
from portfolio_app.import_state import review_control
from portfolio_app.positions import read_snapshot
from portfolio_app.transaction_csv import SIMPLE_FIELDS, TransactionRow, digest, read_csv
from portfolio_app.transaction_import_commands import positions_content, save_transaction_import, import_confirmation_intervals


def control(widget, *args, key, **kwargs):
    return review_control(widget, *args, key='import_tx_'+key, **kwargs)


def _rows(journal):
    mode = control(st.radio, 'Transactions', ['Upload CSV', 'Saved imports'], horizontal=True,
                   key='input') if journal.transaction_imports else 'Upload CSV'
    if mode == 'Saved imports':
        sources = sorted({r.facts['source_id'] for r in journal.transaction_imports})
        source = control(st.selectbox, 'Import', sources, key='saved_source')
        replaced = {r.replaces for r in journal.transaction_imports if r.replaces}
        return tuple(TransactionRow(r.raw, r.facts, r.issues) for r in journal.transaction_imports
                     if r.facts['source_id'] == source and r.id not in replaced)
    upload = st.file_uploader('Transaction CSV', type=['csv'], key='import_tx_upload')
    if upload:
        st.session_state['import_tx_content'] = (upload.name, upload.getvalue())
    content = st.session_state.get('import_tx_content')
    if content:
        st.caption(content[0])
        return read_csv(content[1])
    st.download_button('Download CSV template', ','.join(SIMPLE_FIELDS)+'\n',
                       file_name='transactions.csv', mime='text/csv')
    st.caption('Buy, sell, income, fee, exchange, transfer in/out or starting balance.')
    return ()


def render_transaction_import(directory, *, demo=False):
    directory = Path(directory)
    from portfolio_app.import_ui import clear_import
    st.subheader('Import transactions')
    st.button('Cancel transaction import', on_click=clear_import, key='import_tx_cancel')
    try:
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        rows = _rows(journal)
        if not rows:
            return
        if len({r.facts['source_id'] for r in rows}) != 1:
            raise DataError('Review one source ledger at a time.')
        token = digest([r.identity for r in rows])[:16]+'_'
        from portfolio_app.history_observations import load_observations, ObservationFX
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
        st.caption(f'{len(rows)} transactions · {len({asset for r in rows for _, asset in r.pairs})} assets')
        mapping, additions, listing_changes = render_mapping(directory, snapshot, journal, rows, token, currency=target, demo=demo)
        content = positions_content((directory/'holdings.csv').read_text(encoding='utf-8-sig')
                                    if (directory/'holdings.csv').exists() else '', snapshot, additions, listing_changes=listing_changes)
        holdings = parse_holdings(content)
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
                errors.append({'Reference': row.facts['event_id'], 'Problem': str(exc)})
        from portfolio_app.history_ui import _transaction_table
        active = {i for b in journal.securities for i in b.active_ids}
        if valid:
            st.dataframe(_transaction_table([e for _, e in valid], SimpleNamespace(active_ids=active)),
                         hide_index=True, width='stretch')
        if errors:
            st.dataframe(pd.DataFrame(errors), hide_index=True, width='stretch')
        if st.button('Save evidence only', disabled=bool(conflicts and not corrections), key='import_tx_save_evidence'):
            existing_mapping = {pair: p for pair, p in mapping.items() if p in set(snapshot.holdings.position_id)}
            result = save_transaction_import(directory, rows, existing_mapping,
                expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
                correction_ids=corrections)
            st.session_state.pop(baseline_key, None)
            st.session_state['import_tx_notice'] = f'{result.added} records saved; balances unchanged.'
            st.rerun()
        if notice := st.session_state.get('import_tx_notice'):
            st.caption(notice)
        # Keep the ordinary path short; row selection and ordering are exceptional.
        selected = [r.identity for r, _ in valid]
        with st.expander('Review options'):
            selected = control(st.multiselect, 'Included transactions', selected, default=selected,
                format_func=lambda i: next(r.facts['event_id'] for r, _ in valid if r.identity == i), key=token+'selected')
        if not selected or conflicts and not corrections:
            return
        positions = set(mapping.values())
        books = {b.position_id: b for b in journal.securities}
        from portfolio_app.security_history import selected_events
        supplied_starts = {mapping[r.facts['account_id'], r.facts['asset_id']] for r in rows
            if r.identity in selected and r.facts['event_type'] in ('opening', 'checkpoint')}
        needs_start = [p for p in sorted(positions) if p not in supplied_starts and
            (p not in books or not any(e.kind in ('opening', 'checkpoint') for e in selected_events(books[p])))]
        if needs_start:
            st.caption('Starting holdings · '+', '.join(holdings.set_index('position_id').loc[needs_start, 'name']))
        zero = control(st.checkbox, 'Holdings were zero before these transactions', key=token+'zero') if needs_start else False
        if needs_start and not zero:
            st.caption('Confirm the starting holdings or include a starting balance. Evidence can be saved now.')
            return
        # Collapse multiple owned locations only for genuinely unmapped legacy
        # files; existing bindings and linked cash keep their reviewed semantics.
        aggregate = tuple(sorted({a for a, _ in mapping})) if len({a for a, _ in mapping}) > 1 and not any(b.custody_id for b in books.values() if b.position_id in positions) else ()
        options = dict(selected_ids=selected, starts_at_zero=zero, allow_incomplete=True,
                       correction_ids=corrections, aggregate_accounts=aggregate)
        try:
            preview = prepare_import(journal, holdings, rows, mapping, target=target,
                                     historical=ObservationFX(observations), **options)
        except DataError as exc:
            if 'Matching economic transactions' not in str(exc):
                raise
            distinct = control(st.checkbox, 'Matching transactions are separate executions', key=token+'distinct')
            if not distinct:
                st.warning(str(exc))
                return
            options['accept_duplicates'] = True
            preview = prepare_import(journal, holdings, rows, mapping, target=target,
                                     historical=ObservationFX(observations), **options)
        if not preview.applied:
            st.caption('These transactions are already recorded. Use Transactions to edit or remove them.')
            return
        st.markdown('**Review against portfolio**')
        st.dataframe(pd.DataFrame(preview.comparisons).drop(columns=['position_id']).rename(columns={
            'name':'Position', 'account':'Account', 'before_quantity':'Current units', 'after_quantity':'From history',
            'before_cost':'Current cost', 'after_cost':'From history cost', 'currency':'Currency'}), hide_index=True, width='stretch')
        if preview.cash_changes:
            st.dataframe(pd.DataFrame(preview.cash_changes), hide_index=True, width='stretch')
        comparison = control(st.date_input, 'Balances compared as of', max_value=date.today(), key=token+'comparison')
        accepted = control(st.checkbox, 'Accept the displayed balance changes', key=token+'accept')
        checkpoints = not preview.checkpoints or control(st.checkbox, 'Accept the displayed replacement balances', key=token+'checkpoints')
        if preview.checkpoints:
            st.dataframe(pd.DataFrame(preview.checkpoints), hide_index=True, width='stretch')
        intervals = import_confirmation_intervals(preview.journal, {c['position_id'] for c in preview.comparisons})
        st.caption('Confirm complete history · '+min(a for a, _ in intervals.values())+' – '+max(b for _, b in intervals.values()))
        st.caption('Includes all purchases, sales, income, fees and transfers. Still backfilling? Apply incomplete history.')
        if preview.warnings:
            with st.expander('Transaction details'):
                for warning in preview.warnings:
                    st.caption(warning)
        def apply(complete):
            save_transaction_import(directory, rows, mapping, expected_journal_revision=revision,
                expected_holdings_revision=snapshot.revision, additions=additions, listing_changes=listing_changes, accept_reconciliation=True,
                accept_checkpoints=bool(checkpoints), comparison_date=comparison.isoformat(),
                complete_intervals=intervals if complete else None, expected_observations_revision=observation_revision,
                expected_reporting_currency=target, **options)
            st.session_state['import_tx_pending_complete'] = preview.comparisons[0]['position_id']
            st.rerun()
        with st.container(horizontal=True):
            if st.button('Apply and confirm history', type='primary', disabled=not accepted or not checkpoints, key='import_tx_apply'):
                apply(True)
            if st.button('Apply incomplete history', disabled=not accepted or not checkpoints, key='import_tx_apply_incomplete'):
                apply(False)
    except (DataError, ValueError, UnicodeError, OSError) as exc:
        st.warning(str(exc))
