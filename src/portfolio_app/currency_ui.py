"""Portfolio-currency review and progressively disclosed cost conversion controls."""

from portfolio_app.currency_display import reporting_currency
from decimal import Decimal
from hashlib import sha256

import pandas as pd
import streamlit as st

from portfolio_app.cost_basis import (FIELD, active_components, component, encode_components, estimate_missing,
    freeze_historical, resolve_cost, supplied_conversion)
from portfolio_app.holdings import DataError
from portfolio_app.portfolio_settings import REPORTING_CURRENCIES, load_settings, save_settings
from portfolio_app.storage import revision


def historical_for(directory, *, demo=False):
    from portfolio_app.fx import HistoricalFX
    from portfolio_app.market_data import coordinator
    if demo:
        from portfolio_app.fx import DatedRate
        class OfflineFX:
            def get(self, source, target, day):
                # Explicitly invented FX history, never used for live portfolios.
                rates = {'EUR': 1., 'USD': .9, 'GBP': 1.2}
                if source not in rates or target not in rates:
                    return DatedRate(note='No synthetic history for this currency.')
                return DatedRate(rates[source] / rates[target], day, 'Synthetic demo FX')
        return OfflineFX()
    workspace = str(directory.resolve())
    service = HistoricalFX(directory=directory / '.cache' / 'purchase-fx')

    class BackgroundFX:
        def get(self, source, target, day):
            result, due = service.cached(source, target, day)
            if due:
                coordinator.request(workspace, f'purchase-fx-v1:{source}:{target}:{day}',
                                    lambda: service.get(source, target, day, refresh=True))
            return result
    return BackgroundFX()


def render_currency_setting(directory, settings):
    st.markdown('**Portfolio**')
    currency = st.selectbox('Portfolio currency', REPORTING_CURRENCIES,
        index=REPORTING_CURRENCIES.index(settings.reporting_currency), key='currency_setting_choice')
    if currency != settings.reporting_currency:
        def request_change():
            st.session_state['currency_change_requested'] = currency
            st.session_state['workspace_settings'] = False
        st.button('Review currency change', on_click=request_change)


def reset_currency_views():
    # Keep scope/navigation/targets. Clear amounts and calculations, not percentages.
    for key in list(st.session_state):
        if key.startswith(('position_edit_', 'currency_estimate_')) or key in {
            'planning_amount', 'planning_minimum', 'rebalance_cash', 'rebalance_minimum_purchase', 'rebalance_result', 'portfolio_contribution_result',
            'position_draft'}:
            del st.session_state[key]
    for bucket in ('view_editor_drafts', 'view_editor_bases'):
        drafts = st.session_state.get(bucket, {})
        for key in list(drafts):
            if key.startswith('balance_'):
                del drafts[key]
    st.session_state['currency_notice'] = 'Portfolio currency updated. Monetary planning inputs and previous plans have been cleared.'


def render_currency_dialog(directory, snapshot, settings, prices, historical):
    target = st.session_state.get('currency_change_requested')
    if target is None:
        return False

    def dismiss():
        st.session_state.pop('currency_change_requested', None)
        st.session_state.pop('currency_estimate_review', None)
        st.session_state.pop('currency_setting_choice', None)

    @st.dialog('Change portfolio currency', width='large', on_dismiss=dismiss)
    def review():
        from portfolio_app.portfolio import prepare_portfolio
        from portfolio_app.balances import patch_holdings
        candidate = prepare_portfolio(snapshot.holdings, prices, reporting_currency=target, historical=historical)
        held = candidate.loc[candidate.shares.gt(0)]
        missing = held.loc[held.cost_basis_reporting.isna()]
        st.write(f'{settings.reporting_currency} → {target}')
        st.caption('Original purchases remain in their recorded currencies. Current values use current FX; gains need converted purchase costs.')
        st.write(f'{held.unrealized_gain_reporting.notna().sum()} of {len(held)} positions have comparable gains in {target}.')
        if not missing.empty:
            st.warning(f'{len(missing)} positions have incomplete costs in {target} and will be excluded from gains. Their available current values still count.')
            st.dataframe(missing[['name', 'performance_note']].rename(columns={'name': 'Position', 'performance_note': 'Missing information'}), hide_index=True)
        render_fx_progress(directory)
        labels = dict(zip(missing.position_id, missing.name))
        selection = st.multiselect('Estimate missing conversions for selected positions', list(labels),
            format_func=lambda key: labels[key], key='currency_estimate_selection')
        signature = (target, snapshot.revision, settings.revision, tuple(selection))
        saved = st.session_state.get('currency_estimate_review')
        if selection and st.button('Review FX estimates'):
            try:
                changes, records = {}, []
                for identity in selection:
                    row = held.loc[held.position_id.eq(identity)].iloc[0]
                    parts = estimate_missing(active_components(row), target, prices, historical)
                    changes[identity] = {FIELD: encode_components(parts, row)}
                    for part in parts:
                        conversion = part.get('conversions', {}).get(target, {})
                        if conversion.get('method') == 'estimate':
                            records.append({'Position': row['name'], 'Original cost': f"{Decimal(part['amount']):,.2f} {part['currency']}",
                                f'Cost ({target})': f"{Decimal(conversion['amount']):,.2f}", 'Rate': f"1 {part['currency']} = {Decimal(conversion['rate']):.8g} {target}",
                                'Rate observed': conversion['observed_on']})
                saved = dict(signature=signature, changes=changes, records=records)
                st.session_state['currency_estimate_review'] = saved
            except DataError as exc:
                st.error(str(exc))
        valid_review = saved is not None and saved['signature'] == signature
        confirmed = False
        if selection and valid_review:
            st.dataframe(pd.DataFrame(saved['records']), hide_index=True)
            st.warning('These estimates use current FX for historical costs and may hide currency gains or losses. The reviewed rates will stay fixed.')
            token = sha256(repr(saved).encode()).hexdigest()[:12]
            confirmed = st.checkbox('Use these FX estimates for the selected positions', key='currency_estimate_confirm_' + token)
        if st.button('Apply currency change', type='primary', disabled=bool(selection) and not confirmed):
            try:
                if revision(directory / 'holdings.csv') != snapshot.revision or load_settings(directory).revision != settings.revision:
                    raise DataError('Portfolio changed during review. Reopen the currency review.')
                if selection:
                    patch_holdings(directory / 'holdings.csv', saved['changes'], expected_revision=snapshot.revision)
                save_settings(directory, target, settings.revision)
            except (DataError, OSError) as exc:
                st.error(f'Currency change was not completed: {exc}')
            else:
                st.session_state.pop('currency_change_requested', None)
                st.session_state.pop('currency_setting_choice', None)
                reset_currency_views()
                st.rerun()
        if st.button('Cancel currency change'):
            st.session_state.pop('currency_change_requested', None)
            st.session_state.pop('currency_estimate_review', None)
            st.session_state.pop('currency_setting_choice', None)
            st.rerun()
    review()
    return True


def cost_conversion_controls(parts, target, directory, prices, *, key, demo=False):
    """Used by aggregate entry. Batch inputs use the same resolution/estimate review."""
    if len(parts) != 1 or parts[0]['currency'] == target or not parts[0]['amount']:
        return parts
    part = parts[0]
    st.caption(f'Convert this cost to {target}. For purchases on different dates, use Bulk add purchases or supply their combined converted cost.')
    method = st.selectbox('Purchase conversion', ['Keep saved conversion', 'Leave unresolved', 'Purchase date', 'Exchange rate', 'Converted total cost', 'Latest FX estimate'] if part['date'] or part.get('conversions') else ['Leave unresolved', 'Purchase date', 'Exchange rate', 'Converted total cost', 'Latest FX estimate'], key=key + 'method')
    if method != 'Keep saved conversion':
        part['conversions'].pop(target, None)
    if method == 'Purchase date':
        day = st.date_input('Purchase date', value=__import__('datetime').date.fromisoformat(part['date']) if part['date'] else None, key=key + 'date', max_value=__import__('datetime').date.today())
        part['date'] = day.isoformat() if day else ''
    elif method == 'Exchange rate':
        rate = st.number_input(f'1 {part["currency"]} in {target}', min_value=0., value=None, format='%.8f', key=key + 'rate')
        if rate is not None:
            supplied_conversion(part, target, rate=rate)
    elif method == 'Converted total cost':
        amount = st.number_input(f'Total buy-in ({target})', min_value=0., value=None, format='%.8f', key=key + 'amount')
        if amount is not None:
            supplied_conversion(part, target, amount=amount)
    historical = historical_for(directory, demo=demo)
    if method == 'Latest FX estimate':
        try:
            parts = estimate_missing(parts, target, prices, historical)
            conversion = parts[0]['conversions'].get(target)
            if conversion is None:
                st.caption('Historical conversion is available; no latest-FX estimate is needed.')
                return freeze_historical(parts, target, historical)
            st.warning(f"Estimate: 1 {part['currency']} = {Decimal(conversion['rate']):.8g} {target}; observed {conversion['observed_on']}. This may hide currency gains or losses.")
            token = sha256(repr(conversion).split('saved_at')[0].encode()).hexdigest()[:12]
            if not st.checkbox('Confirm this FX approximation', key=key + 'confirm_' + token):
                parts[0]['conversions'].pop(target, None)
        except DataError as exc:
            st.warning(str(exc))
    resolved = resolve_cost(parts, target, historical)
    if resolved.amount is None:
        st.warning(resolved.note + ' The position remains included in available current-value totals.')
    else:
        st.caption(f'Converted cost: {resolved.amount:,.2f} {target} · {resolved.note or "Recorded cost"}')
    render_fx_progress(directory)
    return freeze_historical(parts, target, historical)


def ui_prices(directory, demo=False):
    from portfolio_app.prices import PriceService, StaticProvider, UnavailableProvider
    from portfolio_app.market_data import prices_for
    if demo:
        try:
            return PriceService(StaticProvider(directory / 'demo_prices.json'))
        except (OSError, ValueError):
            return PriceService(UnavailableProvider())
    return prices_for(directory)


def render_fx_progress(directory):
    from portfolio_app.market_data import coordinator
    workspace = str(directory.resolve())
    stamp = coordinator.revision(workspace)
    pending = coordinator.pending(workspace)

    @st.fragment(run_every=.5 if pending else None)
    def progress():
        if pending and coordinator.revision(workspace) != stamp:
            st.rerun()
        if pending:
            st.caption('Loading exchange rates…')
    progress()
