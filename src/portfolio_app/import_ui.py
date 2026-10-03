"""Experimental snapshot onboarding and optional later market-listing setup."""
from hashlib import sha256
from pathlib import Path

import streamlit as st

from portfolio_app.etf import validate_fund_listings
from portfolio_app.holdings import DataError
from portfolio_app.import_readers import (
    detect_text_format,
    excel_sheets,
    read_table,
    suggest_mapping,
)
from portfolio_app.imports import (
    ImportOptions,
    combine_drafts,
    link_listing,
    normalize_table,
    save_import,
)
from portfolio_app.instruments import InstrumentSearch, catalog_search
from portfolio_app.view_state import persistent_editor
from portfolio_app.import_state import import_files, review_control

FIELD_LABELS = {
    'name': 'Instrument name (required)', 'shares': 'Quantity (required)',
    'isin': 'ISIN', 'wkn': 'WKN', 'ticker': 'Exchange-qualified price ticker', 'account': 'Account / depot',
    'price': 'Snapshot price per unit', 'price_currency': 'Snapshot price currency', 'price_date': 'Snapshot price date',
    'acquisition_price': 'Average buy-in per unit', 'total_cost': 'Total buy-in of remaining position',
    'acquisition_currency': 'Buy-in currency',
}


def _mapping_controls(fields, table, suggestions, prefix):
    mapping = {}
    columns = st.columns(3)
    for index, field in enumerate(fields):
        choices = ['', *table.columns]
        mapping[field] = review_control(columns[index % 3].selectbox,
            FIELD_LABELS[field], choices, index=choices.index(suggestions[field]),
            format_func=lambda item: item or 'Not supplied', key=prefix + field)
    return mapping


def clear_import():
    for key in list(st.session_state):
        if key.startswith('import_'):
            del st.session_state[key]
    for store in ('view_editor_drafts', 'view_editor_bases'):
        for key in list(st.session_state.get(store, {})):
            if key.startswith('import_'):
                del st.session_state[store][key]
    st.session_state['positions_workflow'] = 'Positions'
    st.session_state['positions_workflow_request'] = 'Positions'


def render_import(path, snapshot, funds):
    st.subheader('Import portfolio — experimental')
    st.caption('Current holdings only. Import first; add allocation, targets and classifications later.')
    if not snapshot.holdings.empty:
        st.info('Import currently requires an empty portfolio. Use Update balances for existing positions.')
        return
    st.button('Cancel import', on_click=clear_import)
    st.info('FinanzManager column recognition is provisional. Export a current holdings report, not a transaction ledger. '
            'Check the report date and choose Bank or FinanzManager quantity explicitly if both are present.')
    with st.expander('Export and privacy help'):
        st.markdown('In FinanzManager, look for a current depot/holdings report and **Bericht exportieren**. '
                    'Available formats and columns depend on your version. Use Excel or a delimited text export; '
                    'QIF, PDF and transaction-history exports are not supported.\n\n'
                    'Files are processed locally and held in this session. Only accepted position fields are saved '
                    'in the selected private portfolio directory. Prices from the report remain dated manual prices '
                    'until you explicitly switch to live pricing.')
    uploads = import_files()
    if not uploads:
        return
    st.session_state.setdefault('import_revision', snapshot.revision)
    drafts = []
    failed = False
    for number, (filename, content) in enumerate(uploads):
        token = sha256(content).hexdigest()[:16]
        prefix = f'import_{number}_{token}_'
        with st.expander(f'{number + 1}. {filename}', expanded=True):
            try:
                excel = Path(filename).suffix.lower() in {'.xls', '.xlsx'}
                detected = None if excel else detect_text_format(content)
                settings = st.columns(3)
                decimal_label = review_control(settings[0].selectbox, 'Number format', ['1.234,56 (German)', '1,234.56 (English)'], key=prefix + 'decimal')
                decimal = ',' if 'German' in decimal_label else '.'
                header = review_control(settings[1].number_input, 'Header row', min_value=1, value=1, step=1, key=prefix + 'header')
                sheet, encoding, delimiter = 0, 'utf-8-sig', ';'
                if excel:
                    sheet = review_control(settings[2].selectbox, 'Worksheet', excel_sheets(content), key=prefix + 'sheet')
                else:
                    encodings = ['utf-8-sig', 'cp1252', 'utf-16']
                    encoding = review_control(settings[2].selectbox, 'Text encoding', encodings, index=encodings.index(detected.encoding), key=prefix + 'encoding')
                    delimiter = review_control(st.selectbox, 'Delimiter', [';', '\t', ',', '|'], index=[';', '\t', ',', '|'].index(detected.delimiter),
                                             format_func=lambda item: 'Tab' if item == '\t' else item, key=prefix + 'delimiter')
                    with st.expander('First lines (for header selection)'):
                        st.text('\n'.join(content.decode(encoding).splitlines()[:12]))
                table = read_table(content, excel=excel, sheet=sheet, header_row=int(header), encoding=encoding,
                                   delimiter=delimiter, decimal=decimal)
                if table.empty:
                    raise DataError('No position rows found after this header.')
                schema = sha256(repr((sheet, header, encoding, delimiter, decimal, list(table.columns))).encode()).hexdigest()[:10]
                prefix += schema + '_'
                suggestions = suggest_mapping(table.columns)
                st.caption('Check the column mappings. Unmapped columns are not saved. WKN is not a price ticker.')
                mapping = _mapping_controls(['name', 'shares', 'account'], table, suggestions, prefix)
                with st.expander('Optional identifiers, snapshot prices and buy-ins'):
                    mapping.update(_mapping_controls([field for field in FIELD_LABELS if field not in {'name', 'shares', 'account'}],
                                                     table, suggestions, prefix))
                defaults = {}
                with st.expander('Optional values for blank cells'):
                    st.caption('These values apply to this file only. Leave unknown information blank.')
                    for field, label in [('account', 'Account / depot for this file'), ('price_currency', 'Snapshot currency'),
                                         ('price_date', 'Snapshot date (YYYY-MM-DD)'), ('acquisition_currency', 'Buy-in currency for this file')]:
                        defaults[field] = review_control(st.text_input, label, key=prefix + 'default_' + field)
                use_prices = review_control(st.checkbox, 'Use dated snapshot prices', value=bool(mapping['price']), key=prefix + 'use_prices')
                units = review_control(st.checkbox, 'Quantities are shares/units and prices are amounts per unit (not nominal values or percent quotes)',
                                    key=prefix + 'units')
                st.caption('Correct cells below or uncheck Include for headings, totals and other non-position rows.')
                include_column = 'Include'
                while include_column in table:
                    include_column += ' row'
                editor = table.copy()
                editor.insert(0, include_column, True)
                edited = persistent_editor(editor, key=prefix + 'rows', hide_index=True, width='stretch',
                                           column_config={include_column: st.column_config.CheckboxColumn(required=True)})
                draft = normalize_table(edited.drop(columns=[include_column]),
                                        ImportOptions(mapping, decimal, defaults, use_prices, 'units' if units else ''),
                                        source=f'File {number + 1}', first_row=int(header) + 1,
                                        included=edited[include_column].fillna(False).tolist())
                if not units and draft.included:
                    draft.issues.append(f'File {number + 1}: confirm the quantity/price convention, or exclude unsupported nominal-value positions.')
                drafts.append(draft)
            except (DataError, UnicodeError, OSError) as exc:
                st.error(str(exc))
                failed = True
    draft = combine_drafts(drafts)
    st.subheader('Review import')
    st.write(f'{draft.included} included rows · {draft.excluded} explicitly excluded rows')
    for issue in draft.issues:
        st.error(issue)
    if draft.warnings:
        with st.expander(f'{len(draft.warnings)} missing-information notices'):
            for warning in draft.warnings:
                st.warning(warning)
    if not draft.positions.empty:
        st.dataframe(draft.positions[['name', 'shares', 'account', 'isin', 'wkn', 'ticker', 'manual_price',
                                     'manual_price_currency', 'manual_price_date', 'acquisition_price', 'acquisition_currency']],
                     hide_index=True, width='stretch')
        priced = draft.positions.manual_price.ne('').sum()
        live = (draft.positions.manual_price.eq('') & draft.positions.ticker.ne('')).sum()
        st.caption(f'{priced} dated snapshot prices · {live} live-price tickers (availability not verified). '
                   'Missing prices remain unknown. Allocation and targets are not inferred.')
    if st.button('Import reviewed positions', type='primary', disabled=failed or bool(draft.issues) or draft.positions.empty):
        try:
            save_import(path, draft, expected_revision=st.session_state['import_revision'],
                        validate=lambda frame: validate_fund_listings(frame, funds))
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.session_state['import_pending_complete'] = True
            st.rerun()


def render_import_next_steps():
    if st.session_state.pop('import_pending_complete', False):
        clear_import()
        st.session_state['import_complete'] = True
        st.session_state['main_tabs'] = 'Overview'
    if not st.session_state.get('import_complete'):
        return
    st.success('Portfolio imported. Allocation, targets, buy-ins and classifications can be completed whenever you need them.')
    allocation, prices, done = st.columns(3)
    if allocation.button('Set up allocation'):
        st.session_state['main_tabs'] = 'Rebalance'
        st.session_state['rebalance_tabs'] = 'Targets'
        st.rerun()
    if prices.button('Connect live prices'):
        st.session_state['main_tabs'] = 'Positions'
        st.session_state['positions_workflow_request'] = 'Connect live prices'
        st.rerun()
    if done.button('Dismiss import tips'):
        st.session_state.pop('import_complete', None)
        st.rerun()


def render_listing_link(path, snapshot, funds, *, demo=False):
    st.subheader('Connect live prices')
    if snapshot.holdings.empty:
        st.info('Import or add positions first.')
        return
    instruments = snapshot.holdings.drop_duplicates('id').set_index('id')
    asset_id = st.selectbox('Instrument to link', list(instruments.index),
                            format_func=lambda value: str(instruments.loc[value, 'name']), key='import_link_asset')
    row = instruments.loc[asset_id]
    prefix = f'import_link_{asset_id}_{snapshot.revision}_'
    st.caption('Listing changes apply to all accounts for this instrument. Quantities, costs and allocation assignments stay saved.')
    query = st.text_input('Search by ISIN, WKN, ticker or name', value=row['isin'] or row.get('wkn', '') or row['name'], key=prefix + 'query')
    if st.button('Search listings', key=prefix + 'search'):
        try:
            results = catalog_search(query) if demo else InstrumentSearch(path.parent / '.cache' / 'yahoo').search(query)
            st.session_state[prefix + 'results'] = results
        except Exception:  # noqa: BLE001 -- optional external provider; offline fallback remains usable
            st.session_state[prefix + 'results'] = catalog_search(query)
            st.warning('Live search is unavailable. Showing matching local catalog entries.')
    results = st.session_state.get(prefix + 'results', [])
    if not results:
        st.caption('Search for an exchange listing; missing results do not prevent keeping the imported position.')
        return
    index = st.selectbox('Price listing', range(len(results)), index=None,
                         format_func=lambda value: results[value].label, key=prefix + 'listing')
    if index is None:
        return
    listing = results[index]
    st.write(f'ISIN: {listing.isin or "not yet verified"} · Currency: {listing.currency or "not supplied"}')
    live = st.checkbox('Switch to live prices and clear dated manual prices for this instrument', key=prefix + 'live')
    confirmed = st.checkbox('I confirm this is the same instrument/share class and intended exchange listing', key=prefix + 'confirm')
    if st.button('Save listing', disabled=not confirmed, key=prefix + 'save'):
        try:
            if not demo:
                listing = InstrumentSearch(path.parent / '.cache' / 'yahoo').details(listing)
            link_listing(path, asset_id, listing, expected_revision=snapshot.revision, use_live_prices=live,
                         validate=lambda frame: validate_fund_listings(frame, funds))
        except (DataError, OSError) as exc:
            st.error(str(exc))
        else:
            st.session_state['position_saved_notice'] = 'Listing saved. Live pricing enabled.' if live else 'Listing saved. Existing snapshot prices retained.'
            st.rerun()
