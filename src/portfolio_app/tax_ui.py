"""On-demand Vorabpauschale estimates; ordinary status stays beside results."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.holdings import DataError
from portfolio_app.input_cache import cached_input, file_stamp
from portfolio_app.tax_evidence import (FILE, load_inputs, save_inputs, evidence_key, read_market,
    request_market, cache_path)
from portfolio_app.tax_service import candidates, result_for, combined, observation_market, index_observations
from portfolio_app.vorabpauschale import RULES, EXEMPTIONS, CHURCH_RATES, GUIDANCE, LAW, number, estimate_tax, cents

EXEMPTION_LABELS = ['None / not applied', 'Mixed fund · 15%', 'Equity fund · 30%',
                    'Property fund · 60%', 'Foreign property fund · 80%']
DISTRIBUTIONS = ['Unknown', 'Accumulating / no distributions', 'Market history', 'Manual total']


def money(value):
    return 'Unavailable' if value is None else f'€{cents(value):,.2f}'


@st.fragment(run_every=1)
def _poll(directory, signature, instruments):
    from portfolio_app.market_data import coordinator
    if st.session_state.get('main_tabs') != 'Taxes' or st.session_state.get('tax_selection') != signature:
        return
    if any(coordinator.pending(directory, ('tax', signature[1], instrument)) for instrument in instruments):
        st.caption('Loading tax evidence…')
    else:
        st.rerun()


def _review(directory, raw, revision, item, year, manual, market, lots, fingerprint):
    key = evidence_key(year, item.instrument)
    token = sha256((key+item.key+str(revision)).encode()).hexdigest()[:16]
    prefix = 'tax_review_'+token+'_'
    with st.form(prefix+'form'):
        st.caption('EUR per share · Prices and distributions must use the ending share unit.')
        is_fund = st.checkbox('Investment fund', value=item.is_fund or manual.get('is_fund', False), key=prefix+'fund')
        a, b = st.columns(2)
        start = a.text_input('Year-start price override', value=manual.get('start_price') or '',
                             placeholder=str(market.get('start_price') or 'Missing'), key=prefix+'start')
        end = b.text_input('Year-end price override', value=manual.get('end_price') or '',
                           placeholder=str(market.get('end_price') or 'Missing'), key=prefix+'end')
        basis_options = ['Market-price estimate', 'Redemption prices', 'Statutory market-price fallback']
        basis = st.selectbox('Price basis', basis_options, index=basis_options.index(manual.get('price_basis', basis_options[0])), key=prefix+'basis')
        default_mode = manual.get('distribution_mode', 'Market history' if market.get('distribution_count', 0) else 'Unknown')
        mode = st.selectbox('Distributions', DISTRIBUTIONS, index=DISTRIBUTIONS.index(default_mode), key=prefix+'mode',
            help='Whole-year gross distributions per share, including payments before acquisition. An empty download does not prove zero.')
        a, b = st.columns(2)
        total = a.text_input('Annual distribution override', value=manual.get('distributions') or '', key=prefix+'distribution',
                             help='Used with Manual total. For the ongoing year, enter the amount observed so far.')
        expected = b.text_input('Additional expected distributions', value=manual.get('expected_distributions') or '0', key=prefix+'expected', disabled=year < date.today().year)
        exemption = st.selectbox('Teilfreistellung', list(EXEMPTIONS), index=EXEMPTIONS.index(Decimal(manual.get('exemption', '0'))),
                                format_func=lambda value: EXEMPTION_LABELS[EXEMPTIONS.index(value)], key=prefix+'exemption')
        source = st.text_input('Evidence / assumption', value=manual.get('source', ''), key=prefix+'source',
            help='Identify the fund document or state the assumption. Manual entries are not independently verified.')
        split_review = st.checkbox('Split quantities and per-share units reviewed', value=manual.get('split_units_reviewed', False), key=prefix+'split') if item.holding.splits or market.get('splits') else False
        st.markdown('Remaining acquisition lots')
        ownership_key = evidence_key(year, item.key)
        override = raw.get('ownership', {}).get(ownership_key)
        use_lots = st.checkbox('Use reviewed remaining lots', value=bool(override), key=prefix+'use_lots',
                              help='A calculator-only assumption. Portfolio transactions and balances remain unchanged. Re-review after history changes.')
        rows = override['lots'] if override else [{'quantity': str(l.quantity), 'acquired': l.acquired or ''} for l in lots]
        table = pd.DataFrame([{'Shares': row['quantity'], 'Acquired': row.get('acquired') or ''} for row in rows], columns=['Shares', 'Acquired'])
        edited = st.data_editor(table, num_rows='dynamic', hide_index=True, key=prefix+'lots',
                               column_config={'Acquired': st.column_config.TextColumn(help='YYYY-MM-DD; any known date before the calculation year suffices for older shares.')})
        if st.form_submit_button('Save tax inputs'):
            try:
                update = deepcopy(raw)
                value = dict(is_fund=is_fund, start_price=start.strip(), end_price=end.strip(), price_basis=basis,
                             distribution_mode=mode, distributions=total.strip(), expected_distributions=expected.strip() or '0',
                             exemption=str(exemption), source=source.strip(), split_units_reviewed=split_review)
                if any(value[f] for f in ('start_price', 'end_price', 'distributions')) and not value['source']:
                    raise DataError('Add the evidence source or an explicit assumption.')
                update['funds'][key] = value
                update.setdefault('ownership', {})
                if use_lots:
                    from portfolio_app.vorabpauschale import TaxLot
                    reviewed = []
                    for row in edited.to_dict('records'):
                        qty = number(row['Shares'], 'Shares')
                        acquired = str(row['Acquired']).strip()
                        lot = TaxLot(qty, acquired)
                        if not acquired or acquired > min(f'{year}-12-31', date.today().isoformat()):
                            raise DataError('Supply an acquisition date on or before the calculation boundary.')
                        reviewed.append({'quantity': str(lot.quantity), 'acquired': acquired})
                    update['ownership'][ownership_key] = {'lots': reviewed, 'fingerprint': fingerprint}
                else:
                    update['ownership'].pop(ownership_key, None)
                save_inputs(directory, update, revision)
                st.rerun()
            except (DataError, ValueError) as exc:
                st.error(str(exc))


def render_taxes(directory, holdings, journal, funds=(), valued=None, *, demo=False):
    st.subheader('Vorabpauschale')
    try:
        raw, revision = cached_input('tax_inputs', file_stamp(directory/FILE), lambda: load_inputs(directory))
        a, b = st.columns([1, 3])
        years = [y for y in reversed(RULES) if y <= date.today().year]
        year = a.selectbox('Calculation year', years, key='tax_year')
        forecast = year == date.today().year
        boundary = min(f'{year}-12-31', date.today().isoformat())
        fingerprint = sha256(repr((journal, holdings.to_dict('records'))).encode()).hexdigest()
        all_items = cached_input('tax_candidates', (fingerprint, boundary, repr(funds)),
            lambda: candidates(holdings, journal, boundary, funds, valued))
        # Current quotes change independently of ownership; don't cache them with
        # journal facts. Candidate projection itself does not depend on prices.
        if forecast and valued is not None:
            from dataclasses import replace
            prices = {r['position_id']: r for r in valued.to_dict('records')}
            refreshed = []
            for item in all_items:
                quote = next((prices[p] for p in item.holding.positions if p in prices and prices[p].get('quote_currency') == 'EUR'), {})
                price = quote.get('current_price')
                try:
                    price = number(price) if price is not None else None
                except DataError:
                    price = None
                refreshed.append(replace(item, current_price=price, quote_date=str(quote.get('price_observed_at', ''))[:10]))
            all_items = tuple(refreshed)
        include_other = b.checkbox('Include instruments needing fund classification', key='tax_include_other')
        items = [c for c in all_items if include_other or c.is_fund or raw['funds'].get(evidence_key(year, c.instrument), {}).get('is_fund')]
        accounts = sorted({c.account for c in items})
        account_key = 'tax_accounts_'+str(year)
        if account_key in st.session_state:
            st.session_state[account_key] = [a for a in st.session_state[account_key] if a in accounts]
        chosen_accounts = st.multiselect('Broker / account', accounts, default=accounts, key=account_key)
        items = [c for c in items if c.account in chosen_accounts]
        options = {c.key: c for c in items}
        scope_key = sha256(repr((year, tuple(options))).encode()).hexdigest()[:16]
        selected = st.multiselect('ETFs', list(options), default=list(options), format_func=lambda k: options[k].label, key='tax_funds_'+scope_key)
        items = [options[k] for k in selected]
        if not items:
            st.caption('Select investment funds. Missing classifications can be reviewed above.')
            return
        selection = (str(directory), year, tuple(selected))
        st.session_state['tax_selection'] = selection
        if st.session_state.get('tax_allowance_scope') != selection:
            st.session_state['tax_allowance_scope'] = selection
            st.session_state['tax_allowance'] = 0.
        a, b = st.columns(2)
        allowance = a.number_input(f'Available allowance · {year+1} (€)', min_value=0., step=100., key='tax_allowance',
                                   help='Available for this selection only; deducted once. No loss offsets are modelled.')
        church = b.selectbox('Church tax', list(CHURCH_RATES), index=CHURCH_RATES.index(Decimal(raw['settings'].get('church_rate', '0'))),
                            format_func=lambda r: f'{r:.0%}', key='tax_church')
        if church != Decimal(raw['settings'].get('church_rate', '0')):
            update = deepcopy(raw)
            update['settings']['church_rate'] = str(church)
            save_inputs(directory, update, revision)
            raw, revision = load_inputs(directory)
        if forecast:
            st.caption(f'Forecast · holdings through {boundary} · unchanged prices/shares to year-end · receipt {RULES[year].receipt_date}')
        else:
            st.caption(f'Historical estimate · receipt {RULES[year].receipt_date}')
        retry = st.button('Refresh tax evidence', disabled=demo, key='tax_refresh')
        from portfolio_app.history_observations import load_observations
        observations = cached_input('tax_observations', file_stamp(directory/'history-observations.json'), lambda: index_observations(load_observations(directory)[0]))
        prepared, requested, pending = [], set(), False
        for item in items:
            manual = raw['funds'].get(evidence_key(year, item.instrument), {})
            market = cached_input('tax_market:'+evidence_key(year, item.instrument), file_stamp(cache_path(directory, year, item.instrument)),
                                  lambda: read_market(directory, year, item.instrument))
            if not market:
                market = observation_market(item, observations, year, boundary)
            needs_market = (not manual.get('start_price') or
                (not manual.get('end_price') and not (forecast and item.current_price is not None)) or
                manual.get('distribution_mode', 'Unknown') in ('Unknown', 'Market history'))
            if item.instrument not in requested and not demo and needs_market and (item.is_fund or manual.get('is_fund')):
                requested.add(item.instrument)
                pending |= request_market(directory, year, item.instrument, item.ticker, force=retry)
            inputs, result, lots = result_for(item, year, manual, market, forecast=forecast,
                override=raw.get('ownership', {}).get(evidence_key(year, item.key)), fingerprint=fingerprint)
            prepared.append((item, manual, market, inputs, result, lots))
        gross, tax, count, total = combined([p[4] for p in prepared], Decimal(str(allowance)), church)
        a, b, c = st.columns(3)
        a.metric('Estimated tax' if count == total else 'Partial tax subtotal', money(tax.total) if count else 'Unavailable')
        b.metric('Vorabpauschale', money(gross) if count else 'Unavailable')
        c.metric('Taxable amount', money(tax.taxable) if count else 'Unavailable')
        st.caption(f'{count}/{total} calculated · before loss offsets · Teilfreistellung per fund')
        rows = []
        for item, manual, market, inputs, result, lots in prepared:
            rows.append({'ETF': item.name, 'Account': item.account,
                'Vorabpauschale': money(result.gross), 'Taxable': money(result.taxable),
                'Tax before allowance': money(estimate_tax(result.taxable, church_rate=church).total) if result.taxable is not None else 'Unavailable',
                'Basis': inputs.basis, 'Price date': item.quote_date if forecast and item.current_price is not None and not manual.get('end_price') else market.get('end_date', ''),
                'Status': '; '.join(result.issues) or ('Forecast' if forecast else 'Calculated')})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width='stretch')
        with st.expander('Tax breakdown and calculation'):
            st.write({'Capital gains tax': money(tax.capital_tax), 'Solidarity surcharge': money(tax.solidarity),
                      'Church tax': money(tax.church_tax), 'Allowance used': money(tax.allowance_used),
                      'Basiszins': f'{RULES[year].basis_rate:.2%}'})
            st.caption('Market history is an approximation. No future trades are assumed; expected distributions are entered per fund. No tax is posted.')
            st.markdown(f'[§18 InvStG]({LAW}) · [BMF calculation guidance]({GUIDANCE}) · [Annual rate]({RULES[year].source})')
            st.markdown('[Teilfreistellung](https://www.gesetze-im-internet.de/invstg_2018/__20.html) · [Tax formula](https://www.gesetze-im-internet.de/estg/__32d.html) · [Solidarity surcharge](https://www.gesetze-im-internet.de/solzg_1995/__4.html)')
            st.caption('German private investor, ordinary investment funds. Changes of residence or tax classification, mergers, liquidations and transfers to another owner require separate review.')
        with st.expander('Review inputs'):
            lookup = {p[0].key: p for p in prepared}
            review = st.selectbox('ETF / account to review', list(lookup), format_func=lambda k: lookup[k][0].label, key='tax_review_selected_'+sha256(repr(selected).encode()).hexdigest()[:16])
            item, manual, market, inputs, result, lots = lookup[review]
            st.write({'Year-start price': money(inputs.start_price), 'Year-end price': money(inputs.end_price),
                      'Distributions per share': money(inputs.distributions), 'Annual amount per share': str(result.per_share) if result.per_share is not None else 'Unavailable',
                      'Weighted shares': str(result.weighted_shares) if result.weighted_shares is not None else 'Unavailable'})
            if market.get('source'):
                st.caption(market['source'])
            if market.get('error'):
                st.caption(market['error'])
            if market.get('distribution_note'):
                st.caption(market['distribution_note'])
            _review(directory, raw, revision, item, year, manual, market, lots, fingerprint)
        cache = st.session_state.get('parsed_inputs', {})
        for stale in [k for k in cache if k.startswith('tax_market:')][:-128]:
            cache.pop(stale, None)
        if pending:
            _poll(directory, selection, tuple(requested))
    except (DataError, ValueError) as exc:
        st.error(str(exc))
