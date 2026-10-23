"""Bulk statement review inside the existing position transaction workflow."""
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.consors_pdf import parse_consors_batch, MAX_FILES, MAX_BATCH_BYTES
from portfolio_app.holdings import DataError
from portfolio_app.money import format_money
from portfolio_app.statement_diagnostics import layout_report
from portfolio_app.transaction_imports import draft_problems, possible_duplicate, save_pdf_drafts, identity_review_required


def _money(value):
    return 'Unknown' if value is None else format_money(value.amount, value.currency)



def _charges(values, gross, confirmed):
    if values:
        return ' + '.join(_money(value) for value in values)
    if confirmed and gross is not None:
        return format_money(0, gross.currency)
    return 'Unconfirmed'

def render_pdf_import(directory, row, book, journal, revision, snapshot, key):
    from portfolio_app.history_ui import _history_action
    st.markdown('**Import transactions from PDFs**')
    st.caption(f'Consorsbank buys and sales → {row["name"]}. Files are processed locally.')
    files = st.file_uploader('Choose statements', type=['pdf'], accept_multiple_files=True, key=key+'pdf_files_'+str(st.session_state.get(key+'pdf_upload_revision', 0)))
    if not files:
        return
    if len(files) > MAX_FILES:
        st.warning('Choose up to 100 statements per batch.')
        return
    if sum(f.size for f in files) > MAX_BATCH_BYTES:
        st.warning('Choose a batch smaller than 100 MB.')
        return
    signature = tuple(sha256(f.getvalue()).hexdigest() for f in files)
    cache_key = key+'pdf_parsed'
    if st.session_state.get(cache_key, (None,))[0] != signature:
        with st.spinner('Reading statements…'):
            st.session_state[cache_key] = (signature, parse_consors_batch([f.getvalue() for f in files]))
    drafts = st.session_state[cache_key][1]
    batch_key = sha256(''.join(signature).encode()).hexdigest()[:12]
    charges_confirmed = st.session_state.get(key+'pdf_charges_'+batch_key, False)
    entries, available, similar = [], [], []
    seen_events = list(book.records)
    for index, (file, draft) in enumerate(zip(files, drafts)):
        problems = draft_problems(draft, row, journal)
        duplicate = possible_duplicate(draft, seen_events)
        if not problems:
            available.append(index)
            if duplicate:
                similar.append(index)
            from portfolio_app.transaction_imports import as_event
            seen_events.append(as_event(draft))
        entries.append({'Document':index+1, 'File':file.name, 'Date':draft.trade_date or 'Unknown',
            'Type':(draft.kind or 'Unknown').title(), 'ISIN':draft.isin or 'Unknown',
            'Quantity':str(draft.quantity) if draft.quantity is not None else 'Unknown',
            'Trade value':_money(draft.gross),
            'Listed fees': _charges(draft.fees, draft.gross, charges_confirmed or draft.zero_statement_charges),
            'Listed taxes': _charges(draft.taxes, draft.gross, charges_confirmed or draft.zero_statement_charges),
            'Settlement':_money(draft.settlement),
            'Status':'; '.join(problems) if problems else 'Possible duplicate' if duplicate else
                'Review supplementary details' if draft.review_notes else 'Ready for review'})
    table = pd.DataFrame(entries)
    st.dataframe(table[['Date', 'Type', 'Quantity', 'Trade value', 'Listed fees', 'Status']],
                 hide_index=True, width='stretch')
    with st.expander('Statement details'):
        for note in dict.fromkeys(note for draft in drafts for note in draft.review_notes):
            st.caption(note)
        st.dataframe(table[['Document', 'File', 'ISIN', 'Listed taxes', 'Settlement', 'Status']],
            hide_index=True, width='stretch', column_config={
                'Document': st.column_config.NumberColumn('Document',
                    help='Matches the document number in the masked report for this upload batch.', format='%d')})
        references = [{'Document': i+1, 'Field': ref.field, 'Page': ref.page, 'Row': ref.row}
                      for i, draft in enumerate(drafts) for ref in draft.provenance]
        if references:
            st.caption('Field sources · visual rows counted from the top of each page')
            st.dataframe(pd.DataFrame(references), hide_index=True, width='stretch')
        if any(draft.zero_statement_charges for draft in drafts):
            st.caption('Zero fees confirmed from matching trade value and debit. Costs billed separately need their own records.')
        if any(draft.review_notes or draft.issues != ('Confirm fees and taxes are complete before importing.',)
               or (not draft.fees and not draft.zero_statement_charges) for draft in drafts):
            st.download_button('Download masked layout report',
                data=lambda: layout_report([file.getvalue() for file in files]),
                file_name='statement-layout.txt', mime='text/plain', on_click='ignore',
                key=key+'pdf_layout_report',
                help='For parser troubleshooting. Replaces names, identifiers, dates and amounts with token types; keeps fixed financial labels and amount-comparison results, without the amounts. Review the text before sharing. Originals stay local.')
    if not available:
        st.caption('No importable transactions. Use Add buy / Add sell for unsupported statements or missing fields.')
        return
    chosen = st.multiselect('Transactions to save', available,
        default=[i for i in available if i not in similar], format_func=lambda i: files[i].name,
        key=key+'pdf_selected_'+batch_key)
    st.caption('Check that the selected statements belong to this position and account. Saving leaves current holdings unchanged.')
    identity_needed = any(identity_review_required(drafts[i], row) for i in chosen)
    identity_confirmed = False
    if identity_needed:
        identity_confirmed = st.checkbox('I checked the instrument identifiers', key=key+'pdf_identity_'+batch_key,
            help='No stored ISIN or WKN can be compared for these statements. Check their identifiers against this position.')
    complete = False
    if any(not drafts[i].zero_statement_charges for i in chosen):
        complete = st.checkbox('I checked that all fees and taxes are included', value=False,
            key=key+'pdf_charges_'+batch_key,
            help='Review any supplementary source rows and confirm that no fees or taxes are missing. Leave unchecked to retain unconfirmed charges.')
    allow_similar = False
    if any(i in similar for i in chosen):
        allow_similar = st.checkbox('These similar transactions are distinct trades', key=key+'pdf_similar_'+batch_key)
    def save():
        try:
            save_pdf_drafts(directory, row, journal, revision, snapshot, [drafts[i] for i in chosen],
                            charges_known=complete, allow_similar=allow_similar, identity_confirmed=identity_confirmed)
        except (DataError, ValueError, OSError) as exc:
            st.session_state[key+'save_error'] = str(exc)
            return
        st.session_state.pop(cache_key, None)
        # A new uploader key discards the batch after successful save. Existing
        # history review handles discrepancies and explicit activation.
        st.session_state[key+'pdf_upload_revision'] = st.session_state.get(key+'pdf_upload_revision', 0)+1
        _history_action(key, 'review')
    st.button('Save transactions and review', type='primary', disabled=not chosen or
        (identity_needed and not identity_confirmed) or
        (any(i in similar for i in chosen) and not allow_similar), key=key+'pdf_save', on_click=save)
