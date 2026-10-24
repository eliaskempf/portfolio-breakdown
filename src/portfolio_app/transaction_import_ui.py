"""Local, per-statement review of a bulk PDF import."""
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.consors_pdf import parse_consors_batch, local_review_rows, MAX_FILES, MAX_BATCH_BYTES
from portfolio_app.holdings import DataError
from portfolio_app.money import format_money
from portfolio_app.statement_diagnostics import layout_report
from portfolio_app.transaction_imports import draft_problems, possible_duplicate, save_pdf_drafts, identity_review_required


def _money(value):
    return 'Unknown' if value is None else format_money(value.amount, value.currency)


def _charges(values, gross, confirmed):
    if values:
        totals = {}
        for value in values:
            totals[value.currency] = totals.get(value.currency, 0) + value.amount
        return ' + '.join(format_money(amount, currency) for currency, amount in totals.items())
    if confirmed and gross is not None:
        return format_money(0, gross.currency)
    return 'Not listed'


def _decide(key, identity, decision):
    decisions = dict(st.session_state.get(key+'decisions', {}))
    decisions[identity] = decision
    st.session_state[key+'decisions'] = decisions


def _focus(key, index):
    st.session_state[key+'focus'] = index
    st.session_state[key+'table_revision'] = st.session_state.get(key+'table_revision', 0)+1


def _include(key, index):
    choices = dict(st.session_state.get(key+'include_choices', {}))
    choices[index] = st.session_state[key+str(index)+'_include']
    st.session_state[key+'include_choices'] = choices


def _statement_details(files, drafts, key):
    with st.expander('Statement details'):
        st.dataframe(pd.DataFrame([{'Document': i+1, 'File': file.name,
            'ISIN': draft.isin or 'Not supplied', 'WKN': draft.wkn or 'Not supplied'}
            for i, (file, draft) in enumerate(zip(files, drafts))]), hide_index=True, width='stretch')
        st.download_button('Download masked layout report',
            data=lambda: layout_report([file.getvalue() for file in files]),
            file_name='statement-layout.txt', mime='text/plain', key=key+'report',
            help='For troubleshooting only. Names, identifiers, dates and amounts are masked. Originals stay local.')


def render_pdf_import(directory, row, book, journal, revision, snapshot, key):
    from portfolio_app.history_ui import _history_action
    st.markdown('**Import transactions from PDFs**')
    st.caption(f'Consorsbank buys and sales → {row["name"]}')
    files = st.file_uploader('Choose statements', type=['pdf'], accept_multiple_files=True,
        key=key+'pdf_files_'+str(st.session_state.get(key+'pdf_upload_revision', 0)))
    if not files:
        return
    if len(files) > MAX_FILES or sum(f.size for f in files) > MAX_BATCH_BYTES:
        st.warning('Choose up to 100 statements / 100 MB per batch.')
        return
    signature = tuple(sha256(f.getvalue()).hexdigest() for f in files)
    cache_key = key+'pdf_parsed'
    if st.session_state.get(cache_key, (None,))[0] != signature:
        with st.spinner('Reading statements…'):
            st.session_state[cache_key] = (signature, parse_consors_batch([f.getvalue() for f in files]))
    drafts = st.session_state[cache_key][1]
    review_key = key+'pdf_review_'+sha256(''.join(signature).encode()).hexdigest()[:12]+'_'
    decisions = st.session_state.get(review_key+'decisions', {})
    choices = st.session_state.get(review_key+'include_choices', {})
    problems, similar, included = {}, set(), []
    known = list(book.records)
    for i, draft in enumerate(drafts):
        problems[i] = draft_problems(draft, row, journal)
        if not problems[i]:
            if possible_duplicate(draft, known):
                similar.add(i)
            from portfolio_app.transaction_imports import as_event
            known.append(as_event(draft))
        include_key = review_key+str(i)+'_include'
        use = choices.get(i, not problems[i] and i not in similar)
        st.session_state.setdefault(include_key, use)
        if use and not problems[i]:
            included.append(i)
    confirmed = {fingerprint for fingerprint, decision in decisions.items() if decision == 'confirmed'}
    unchecked = {fingerprint for fingerprint, decision in decisions.items() if decision == 'unchecked'}
    distinct = set(st.session_state.get(review_key+'distinct', ()))
    def complete(i):
        return drafts[i].statement_charges_known or drafts[i].zero_statement_charges or drafts[i].fingerprint in confirmed
    def needs_review(i):
        return (not complete(i) and drafts[i].fingerprint not in unchecked
                or i in similar and drafts[i].fingerprint not in distinct)
    def status(i):
        if problems[i]:
            return 'Already imported' if 'Already imported.' in problems[i] else 'Cannot import'
        if i not in included:
            return 'Similar trade · excluded' if i in similar else 'Excluded'
        if needs_review(i):
            return 'Check statement'
        return 'Ready' if complete(i) else 'Charges unchecked'
    pending = [i for i in included if needs_review(i)]
    ready = [i for i in included if not needs_review(i)]
    st.caption(f'{len(ready)} ready · {len(pending)} to check · {len(files)-len(included)} excluded')
    # Dates remain ISO strings, so header sorting is chronological too. Stable
    # source indices keep table ordering separate from within-day event order.
    ordered = sorted(range(len(drafts)), key=lambda i: (drafts[i].trade_date or '', drafts[i].trade_time or ''), reverse=True)
    entries = [{'File': files[i].name, 'Date': drafts[i].trade_date or 'Unknown',
        'Type': (drafts[i].kind or 'Unknown').title(),
        'Shares': str(drafts[i].quantity) if drafts[i].quantity is not None else 'Unknown',
        'Trade value': _money(drafts[i].gross), 'Fees': _charges(drafts[i].fees, drafts[i].gross, complete(i)),
        'Taxes': _charges(drafts[i].taxes, drafts[i].gross, complete(i)),
        'Settlement': _money(drafts[i].settlement), 'Status': status(i)} for i in ordered]
    selected = st.dataframe(pd.DataFrame(entries), hide_index=True, width='stretch',
        on_select='rerun', selection_mode='single-row',
        key=review_key+'table_'+str(st.session_state.get(review_key+'table_revision', 0)),
        column_config={'File':st.column_config.TextColumn('Statement', help='Select a row to inspect or exclude that statement.')})
    selected_rows = selected.selection.rows
    focus = ordered[selected_rows[0]] if selected_rows else st.session_state.get(review_key+'focus', pending[0] if pending else ordered[0])
    draft = drafts[focus]
    st.markdown('**'+('Check statement' if focus in pending else 'Statement')+'**')
    st.text(files[focus].name)
    with st.container(horizontal=True):
        st.checkbox('Include this transaction', key=review_key+str(focus)+'_include', disabled=bool(problems[focus]),
                    on_change=_include, args=(review_key,focus))
        others = [i for i in pending if i != focus]
        if others:
            st.button('Next statement to check', on_click=_focus, args=(review_key, others[0]))
    if problems[focus]:
        for problem in problems[focus]:
            st.error(problem)
        st.caption('Use Add buy / Add sell for unsupported statements. No transaction will be saved from this file.')
    else:
        _review_statement(files[focus], draft, focus, review_key, complete(focus), decisions,
                          similar=focus in similar, distinct=draft.fingerprint in distinct)
    _statement_details(files, drafts, review_key)
    if not any(not p for p in problems.values()):
        st.info('No importable transactions. Use Add buy / Add sell for unsupported statements or missing fields.')
        return
    identity_confirmed = False
    if any(identity_review_required(drafts[i], row) for i in included):
        st.caption('This position has no comparable identifier. Check the ISIN/WKN in Statement details against the intended asset.')
        identity_confirmed = st.checkbox('I checked the instrument identifiers', key=review_key+'identity')
    identity_needed = any(identity_review_required(drafts[i], row) for i in included)
    if pending:
        st.caption(f'Check {len(pending)} selected statement(s), keep their charges unchecked, or exclude them before saving.')
    else:
        unknown = sum(not complete(i) for i in included)
        st.caption(f'Save {len(included)} transactions'+(f' · {unknown} with unchecked charges' if unknown else '')+'. Holdings change only after reconciliation.')
    def save():
        try:
            save_pdf_drafts(directory, row, journal, revision, snapshot, [drafts[i] for i in included],
                confirmed_charge_ids=confirmed, confirmed_similar_ids=distinct, identity_confirmed=identity_confirmed)
        except (DataError, ValueError, OSError) as exc:
            st.session_state[key+'save_error'] = str(exc)
            return
        st.session_state.pop(cache_key, None)
        st.session_state[key+'pdf_upload_revision'] = st.session_state.get(key+'pdf_upload_revision', 0)+1
        _history_action(key, 'review')
    st.button('Save transactions and review', type='primary',
        disabled=not included or bool(pending) or (identity_needed and not identity_confirmed),
        key=key+'pdf_save', on_click=save)


def _review_statement(file, draft, index, key, complete, decisions, *, similar, distinct):
    from portfolio_app.consors_pdf import settlement_difference
    money = pd.DataFrame([{'Trade value': _money(draft.gross),
        'Fees': _charges(draft.fees, draft.gross, complete),
        'Taxes': _charges(draft.taxes, draft.gross, complete),
        'Debit' if draft.kind == 'buy' else 'Credit': _money(draft.settlement)}])
    st.table(money)
    delta = settlement_difference(draft.kind, draft.gross, draft.settlement, draft.fees, draft.taxes)
    if delta == 0:
        st.caption('Trade value '+('+' if draft.kind == 'buy' else '−')+' listed fees and taxes = settlement.')
    elif delta is not None:
        # Never round a sub-cent discrepancy to a misleading zero.
        st.caption(f'Settlement difference: {delta:f} {draft.gross.currency}. No balancing fee is added.')
    if similar and not distinct:
        st.warning('A transaction with the same date, shares and trade value already exists.')
        def distinguish():
            st.session_state[key+'distinct'] = tuple(set(st.session_state.get(key+'distinct', ())) | {draft.fingerprint})
            st.session_state[key+str(index)+'_include'] = True
            _include(key,index)
        st.button('Import as a separate trade', on_click=distinguish, key=key+'distinct_'+str(index))
    if draft.review_notes:
        st.markdown('**Additional statement rows**')
        st.caption('These rows were not used as fees, taxes or traded shares. Check whether they are calculation details or contain an additional payment.')
        try:
            context = local_review_rows(file.getvalue(), draft)
            st.table(pd.DataFrame([{'Location': f'Page {page}, row {row}', 'Statement text': text}
                                  for page, row, field, text in context]))
        except Exception:
            st.warning('Could not display the additional rows. Check the original PDF before confirming.')
    with st.expander('Extracted amounts and sources'):
        st.caption('ISIN: '+(draft.isin or 'Not supplied')+' · WKN: '+(draft.wkn or 'Not supplied'))
        st.caption('Quoted unit price: '+_money(draft.unit_price))
        charges = [{'Type':kind, 'Amount':_money(value)} for kind, values in (('Fee',draft.fees),('Tax',draft.taxes)) for value in values]
        if charges:
            st.table(pd.DataFrame(charges))
        st.dataframe(pd.DataFrame([{'Field':ref.field,'Page':ref.page,'Row':ref.row} for ref in draft.provenance]), hide_index=True)
    if not complete:
        if not draft.review_notes:
            for reason in draft.charge_review_reasons:
                st.caption(reason)
        st.caption('Not listed means no amount was extracted; it does not mean an extra tax is expected. Confirm only if all statement charges are accounted for.')
        decision = decisions.get(draft.fingerprint)
        if decision == 'unchecked':
            st.caption('Will save with unchecked charges; complete gain calculations may remain unavailable.')
        with st.container(horizontal=True):
            st.button('Confirm statement charges', on_click=_decide, args=(key,draft.fingerprint,'confirmed'),
                key=key+'confirm_'+str(index), help='I checked this statement. No fees or taxes are missing from the extracted amounts.')
            st.button('Keep charges unchecked', on_click=_decide, args=(key,draft.fingerprint,'unchecked'),
                key=key+'unchecked_'+str(index), disabled=decision == 'unchecked')
    elif draft.fingerprint in decisions:
        st.caption('Statement charges confirmed by you.')
        st.button('Undo confirmation', on_click=_decide, args=(key,draft.fingerprint,'pending'), key=key+'undo_'+str(index))
    else:
        st.caption('Statement charges reconciled. Separately billed costs need their own transaction.')
