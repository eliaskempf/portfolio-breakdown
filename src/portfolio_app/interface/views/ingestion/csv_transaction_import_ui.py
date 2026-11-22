"""CSV extraction and mapping feeding the ordinary transaction history."""
from datetime import date
from pathlib import Path

import pandas as pd
import streamlit as st

from portfolio_app.ledger.account_journal import load_journal
from portfolio_app.ingestion.csv_transaction_imports import event_for, prepare_import
from portfolio_app.portfolio.holdings import DataError, parse_holdings
from portfolio_app.interface.shared.import_state import review_control, clear_transaction_draft
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.workspace.storage import revision as file_revision
from portfolio_app.ingestion.transaction_csv import SIMPLE_FIELDS, TransactionRow, digest, read_csv, retain_import_interpretations
from portfolio_app.ledger.transaction_import_commands import positions_content, save_transaction_import


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
        if journal.kept_imports:
            accepted = {r.id for r in journal.transaction_imports}
            kept = {r.id: r for r in journal.kept_imports if r.facts['source_id'] == source and r.id not in accepted}
            if kept:
                choices = {r.id: f'{i}. {r.facts["event_id"]} · {r.facts["asset_id"]} · '
                           f'{r.facts.get("effective_date") or r.facts["timestamp_utc"][:10]}'
                           for i, r in enumerate(kept.values(), 1)}
                chosen = control(st.selectbox, 'Reconsider kept correction', list(kept), index=None,
                                 placeholder='Choose a saved decision', format_func=choices.get,
                                 key='kept_choice_'+digest(list(kept))[:16],
                                 help='Compare the previously declined source with the current transaction.')
                if chosen:
                    receipt = kept[chosen]
                    return (TransactionRow(receipt.raw, receipt.facts, receipt.issues),), receipt.id
        replaced = {r.replaces for r in journal.transaction_imports if r.replaces}
        return tuple(TransactionRow(r.raw, r.facts, r.issues) for r in journal.transaction_imports
                     if r.facts['source_id'] == source and r.id not in replaced), None
    upload = st.file_uploader('Transaction CSV', type=['csv'], key='import_tx_upload')
    if upload:
        st.session_state['import_tx_content'] = (upload.name, upload.getvalue())
    content = st.session_state.get('import_tx_content')
    if content:
        st.caption(content[0])
        st.button('Discard review', on_click=clear_transaction_draft, key='import_tx_discard',
                  help='Clear the uploaded file and unsaved review choices. Changing tabs keeps the review.')
        return read_csv(content[1]), None
    st.download_button('Download CSV template', ','.join(SIMPLE_FIELDS)+'\n',
                       file_name='transactions.csv', mime='text/csv')
    return (), None


def _changed_sources(rows, journal, token, revision, reconsider):
    latest = {(r.facts['source_id'], r.facts['event_id']): r for r in journal.transaction_imports}
    retained = {r.id for r in journal.transaction_imports}
    kept = {r.id for r in journal.kept_imports} - retained
    conflicts = {r.identity: r for r in rows if all(r.key) and r.key in latest
                 and r.identity not in retained and (r.identity not in kept or r.identity == reconsider)}
    corrections, decisions = [], []
    pending = 0
    if conflicts:
        owners = {e.id: b for b in journal.securities for e in b.records}
        events_by_id = {(b.position_id, e.id): e for b in journal.securities for e in b.records}
        from portfolio_app.ledger.security_history import latest_records
        lineages = {b.position_id: latest_records(b) for b in journal.securities}
        receipts = {r.id: r for r in journal.transaction_imports}
        for row in conflicts.values():
            previous = latest[row.key]
            label = f'{row.date} · {row.facts["asset_id"]} · {row.facts["event_type"].replace("_", " ").title()}'
            with st.expander(f'Changed transaction · {label} · {row.key[1]}'):
                changes = [{'Field': key.replace('_', ' ').capitalize(),
                            'Saved source': previous.facts.get(key) or 'Unknown',
                            'New source': value or 'Unknown'}
                           for key, value in row.facts.items() if value != previous.facts.get(key)
                           and key not in ('source_id', 'event_id', 'schema_version', 'group_id')]
                st.table(pd.DataFrame(changes), hide_index=True)
                ancestor = previous
                while ancestor.id not in owners and ancestor.replaces:
                    ancestor = receipts[ancestor.replaces]
                book = owners.get(ancestor.id)
                current = events_by_id.get((book.position_id, ancestor.id)) if book else None
                if current is not None:
                    roots, latest_events = lineages[book.position_id]
                    current = latest_events.get(roots[current.id], current)
                    # A pending source revision may itself replace a user's
                    # active edit/removal. Stop at accepted source authority;
                    # older superseded edits were already reviewed then.
                    while current.id in receipts and current.id not in book.active_ids and current.replaces:
                        current = events_by_id[book.position_id, current.replaces]
                if current is not None and current.id not in receipts:
                    if current.kind == 'void':
                        st.warning('Use correction restores your removed transaction.')
                    else:
                        st.warning('Use correction replaces your later edit.')
                        from portfolio_app.interface.views.history.history_ui import _transaction_table
                        events, labels = [current], ['Current history']
                        try:
                            mapped = {(a, asset): p for a, asset, p in previous.positions}
                            events.append(event_for(row, mapped))
                            labels.append('New source')
                        except (DataError, ValueError):
                            pass  # The ordinary row validation below shows the problem.
                        table = _transaction_table(events, book).drop(columns='Status')
                        table.insert(0, 'Version', labels)
                        st.table(table, hide_index=True)
                choice = control(st.radio, 'Source decision', ['Keep current', 'Use correction'], horizontal=True,
                                 index=0 if row.identity == reconsider else None, label_visibility='collapsed',
                                 key=f'{token}correction_{row.identity}_{revision}',
                                 help='Keep current remembers these exact source facts. Use correction sends them to review.')
                if choice is None:
                    pending += 1
                elif choice == 'Use correction':
                    corrections.append(row.identity)
                elif row.identity not in kept:
                    decisions.append(row.identity)
    return corrections, decisions, kept.difference(corrections).union(decisions), pending


def render_transaction_import(directory, *, demo=False):
    directory = Path(directory)
    try:
        if draft := st.session_state.get('import_tx_review'):
            _review_import(directory, draft)
            return
        st.subheader('Import transactions')
        journal, revision = load_journal(directory)
        snapshot = read_snapshot(directory/'holdings.csv')
        source_rows, reconsider = _rows(journal)
        # Saved decisions already carry the exact source interpretation.
        rows = source_rows if reconsider else retain_import_interpretations(source_rows, journal.transaction_imports, journal.kept_imports)
        if not rows:
            return
        if len({r.facts['source_id'] for r in rows}) != 1:
            raise DataError('Review one source ledger at a time.')
        token = digest([r.identity for r in rows])[:16]+'_'
        from portfolio_app.performance.history_observations import load_observations
        from portfolio_app.portfolio.portfolio_settings import load_settings
        observations, observation_revision = load_observations(directory)
        target = load_settings(directory).reporting_currency
        baseline = revision, snapshot.revision, observation_revision, target, file_revision(directory/'allocation.yaml')
        baseline_key = 'import_tx_baseline_'+token
        if st.session_state.setdefault(baseline_key, baseline) != baseline:
            st.warning('Portfolio changed. Reload this review before applying.')
            if st.button('Reload transaction review'):
                st.session_state.pop(baseline_key, None)
                st.rerun()
            return
        corrections, keep_ids, kept, pending = _changed_sources(rows, journal, token, revision, reconsider)
        reconsider_ids = [reconsider] if reconsider in corrections else []
        working_rows = [r for r in rows if r.identity not in kept] if kept else rows
        previously_kept = len({r.identity for r in rows if r.identity in kept} - set(keep_ids)) if kept else 0
        if previously_kept:
            st.caption(f'Kept current · {previously_kept}', help='Reconsider a saved decision under Saved imports.')
        if keep_ids:
            st.caption(f'Keep on save · {len(keep_ids)}')
        def save_decisions():
            if st.button('Save decisions', type='primary', key='import_tx_save_decisions'):
                decisions = set(keep_ids)
                save_transaction_import(directory, [r for r in rows if r.identity in decisions], {},
                                        expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
                                        keep_ids=decisions)
                st.session_state.pop(baseline_key, None)
                st.rerun()
        if not working_rows:
            if keep_ids:
                save_decisions()
            return
        from portfolio_app.interface.views.ingestion.transaction_mapping_ui import render_mapping
        mapping, additions, listing_changes = render_mapping(directory, snapshot, journal, working_rows, token, currency=target, demo=demo)
        content = positions_content((directory/'holdings.csv').read_text(encoding='utf-8-sig')
                                    if (directory/'holdings.csv').exists() else '', snapshot, additions, listing_changes=listing_changes)
        proposed_holdings = parse_holdings(content)
        from portfolio_app.ledger.transaction_import_commands import _validate_categories
        _validate_categories(directory, proposed_holdings)
        valid, errors = [], []
        for row in working_rows:
            try:
                valid.append((row, event_for(row, mapping)))
            except (DataError, ValueError) as exc:
                problem = 'Choose a holding for every asset used by this transaction.' if str(exc).startswith('Map the custody position') else str(exc)
                errors.append({'Date': row.date or 'Unknown', 'Asset': row.facts['asset_id'], 'Problem': problem})
        from portfolio_app.interface.views.ingestion.transaction_review_ui import render_transactions
        active = {i for b in journal.securities for i in b.active_ids}
        if valid:
            render_transactions(valid, active, {r.id for r in journal.transaction_imports}, token)
        if errors:
            st.markdown('**Transactions needing attention**')
            st.dataframe(pd.DataFrame(errors), hide_index=True, width='stretch')
        # Keep the ordinary path short; row selection and ordering are exceptional.
        selected = [r.identity for r, _ in valid]
        advanced = st.expander('Import options')
        with advanced:
            from portfolio_app.interface.views.history.history_ui import _event_label
            labels = {r.identity: f'{number}. {r.facts["asset_id"]} · {_event_label(event)}'
                      for number, (r, event) in enumerate(valid, 1)}
            selected = control(st.multiselect, 'Included transactions', selected, default=selected,
                format_func=labels.get, key=token+'selected')
        positions = set(mapping.values()) if selected else set()
        books = {b.position_id: b for b in journal.securities}
        from portfolio_app.ledger.security_history import selected_events
        supplied_starts = {mapping[r.facts['account_id'], r.facts['asset_id']] for r in working_rows
            if r.identity in selected and r.facts['event_type'] in ('opening', 'checkpoint')}
        needs_start = [p for p in sorted(positions) if p not in supplied_starts and
            (p not in books or not any(e.kind in ('opening', 'checkpoint') for e in selected_events(books[p])))]
        zero = False
        if needs_start:
            start = control(st.selectbox, 'Before the first transaction', ['Zero holdings', 'Earlier holdings'],
                key=token+'start', help='Use zero only when the export starts at the first acquisition. An incomplete export needs a recorded starting balance.')
            zero = start == 'Zero holdings'
        # Collapse multiple owned locations only for genuinely unmapped legacy
        # files; existing bindings and linked cash keep their reviewed semantics.
        aggregate = tuple(sorted({a for a, _ in mapping})) if len({a for a, _ in mapping}) > 1 and not any(b.custody_id for b in books.values() if b.position_id in positions) else ()
        options = dict(selected_ids=selected, starts_at_zero=zero, allow_incomplete=True,
                       correction_ids=corrections, keep_ids=keep_ids, reconsider_ids=reconsider_ids, aggregate_accounts=aggregate)
        blockers = []
        if errors:
            blockers.append(f'Resolve the {len(errors)} transaction {"row" if len(errors) == 1 else "rows"} needing attention.')
        if pending:
            blockers.append(f'Choose a decision for {pending} changed transaction' + ('s.' if pending != 1 else '.'))
        keep_only = bool(keep_ids and not selected and not pending and not corrections)
        if not selected and not errors and not keep_only:
            blockers.append('Select at least one transaction in Import options.')
        if needs_start and not zero:
            blockers.append('Record the earlier starting balance in the position’s Transactions view, or include it in the CSV.')
        if blockers:
            st.caption(' '.join(blockers))
        actions = st.container(horizontal=True)
        if keep_only:
            with actions:
                save_decisions()
        elif actions.button('Review against portfolio', type='primary',
                          disabled=bool(blockers), key='import_tx_review_start'):
            st.session_state['import_tx_review'] = dict(rows=rows, mapping=mapping, additions=additions,
                listing_changes=listing_changes, options=options, token=token, baseline=baseline,
                needs_start=needs_start, zero=zero)
            st.rerun()
        if not reconsider_ids and actions.button('Save evidence only', disabled=bool(pending), key='import_tx_save_evidence'):
            existing_mapping = {pair: p for pair, p in mapping.items() if p in set(snapshot.holdings.position_id)}
            result = save_transaction_import(directory, rows, existing_mapping,
                expected_journal_revision=revision, expected_holdings_revision=snapshot.revision,
                correction_ids=corrections, keep_ids=keep_ids)
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
    from portfolio_app.performance.history_observations import load_observations, ObservationFX
    from portfolio_app.portfolio.portfolio_settings import load_settings
    from portfolio_app.interface.views.history.history_review_ui import ReviewPreview, render_history_review
    journal, revision = load_journal(directory)
    snapshot = read_snapshot(directory/'holdings.csv')
    observations, observation_revision = load_observations(directory)
    target = load_settings(directory).reporting_currency
    allocation_revision = file_revision(directory/'allocation.yaml')
    baseline = revision, snapshot.revision, observation_revision, target, allocation_revision
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
    review_key = 'import_tx_'+draft['token']+'review_'
    from portfolio_app.ledger.history_order import original_order_ids, editable_order_ids
    def options(excluded):
        excluded = original_order_ids(excluded, st.session_state.get(review_key+'order', {}))
        previously_kept = set(draft['options'].get('reconsider_ids', ()))
        stay_kept = previously_kept.intersection(excluded)
        return {**draft['options'], 'selected_ids':[i for i in draft['options']['selected_ids'] if i not in excluded],
                'reconsider_ids': previously_kept - stay_kept, 'correction_ids': set(draft['options']['correction_ids']) - stay_kept,
                'excluded_ids': excluded - stay_kept, 'accept_duplicates':True,
                'order_overrides': st.session_state.get(review_key+'order', {})}
    def build(excluded):
        preview = prepare_import(journal, holdings, draft['rows'], draft['mapping'], target=target,
            historical=ObservationFX(observations), **options(excluded))
        note = ''
        if draft['zero'] and preview.applied:
            names = holdings.set_index('position_id')['name']
            note = 'Starts at zero · '+', '.join(names.loc[draft['needs_start']])
        return ReviewPreview(preview.journal, preview.comparisons if preview.applied else (),
            cash=preview.cash_changes, matches=preview.matches,
            editable=editable_order_ids(preview.journal.securities, set(draft['options']['selected_ids'])-active,
                st.session_state.get(review_key+'order', {})),
            known_positions=frozenset(snapshot.holdings.position_id),
            checkpoints=preview.checkpoints, warnings=preview.warnings, starting_note=note, excluded=preview.excluded)
    def apply(excluded):
        save_transaction_import(directory, draft['rows'], draft['mapping'], expected_journal_revision=revision,
            expected_holdings_revision=snapshot.revision, additions=draft['additions'], listing_changes=draft['listing_changes'],
            accept_reconciliation=True, accept_checkpoints=True, comparison_date=date.today().isoformat(),
            expected_observations_revision=observation_revision,
            expected_allocation_revision=allocation_revision,
            expected_reporting_currency=target, **options(excluded))
        st.session_state['import_tx_pending_complete'] = True
        _return_to_import()
        st.rerun()
    render_history_review(build, key=review_key, apply=apply, keep=_return_to_import, keep_label='Back to import',
                          decisions_pending=bool(draft['options'].get('keep_ids')))
