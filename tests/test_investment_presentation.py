"""Synthetic capital attribution preserves contract and currency risk inputs."""
import pandas as pd
import pytest

from portfolio_app.investment_presentation import investment_exposure, investment_risk_positions
from portfolio_app.portfolio_exposure import PortfolioExposure


def invented_views():
    raw = pd.DataFrame([
        dict(position_id='cash', id='cash', name='Synthetic cash', instrument_type='cash',
             bucket_id='', shares=1., quote_currency='USD', current_value_native=21000.,
             current_value_reporting=18900., current_price=21000.),
        dict(position_id='old', id='old', name='Synthetic old contract', instrument_type='future',
             bucket_id='equity', shares=1., quote_currency='USD', current_value_native=0.,
             current_value_reporting=0., current_price=0.),
        dict(position_id='new', id='new', name='Synthetic new contract', instrument_type='future',
             bucket_id='equity', shares=1., quote_currency='USD', current_value_native=1000.,
             current_value_reporting=900., current_price=1000.),
    ])
    raw.attrs = dict(actual_accounts=True, reporting_currency='EUR')
    displayed = raw.iloc[[0]].copy()
    displayed.loc[:, 'current_value_native'] = 1000.
    displayed.loc[:, 'current_value_reporting'] = 900.
    investment = dict(raw.iloc[2], position_id='investment', id='investment',
                      name='Synthetic investment', current_value_native=21000., current_value_reporting=18900.)
    displayed = pd.concat([displayed, pd.DataFrame([investment])], ignore_index=True)
    displayed.attrs = dict(raw.attrs, investment_members={'investment': ('old', 'new')},
        investment_source_map={'old': 'investment', 'new': 'investment'},
        investment_cash={'investment': {'cash': {'native': 20000., 'reporting': 18000.}}})
    return raw, displayed


def test_grouped_exposure_preserves_gross_and_uses_allocated_capital():
    raw, displayed = invented_views()
    capital = pd.DataFrame([dict(source_position_id=row.position_id, name=row.name,
        bucket_id=row.bucket_id, value=row.current_value_reporting, kind='owned asset') for row in raw.itertuples()])
    exposures = pd.DataFrame([
        dict(source_position_id='old', bucket_id='equity', exposure_reporting=-10000., market_exposure_reporting=-10000.),
        dict(source_position_id='new', bucket_id='equity', exposure_reporting=30000., market_exposure_reporting=30000.),
    ])
    original = PortfolioExposure(capital, exposures, 19800., 0., 0., 19800., (), True)
    result = investment_exposure(original, displayed)
    assert result.gross_exposure == original.gross_exposure == 40000.
    assert result.net_equity == original.net_equity
    assert result.selected_multiple({'equity'}) == pytest.approx(20000 / 18900)
    assert result.exposures.source_position_id.tolist() == ['investment', 'investment']
    assert original.exposures.source_position_id.tolist() == ['old', 'new']
    assert result.capital.value.sum() == original.capital.value.sum()


def test_risk_decomposition_keeps_exact_contracts_and_allocated_currency_cash():
    raw, displayed = invented_views()
    result = investment_risk_positions(displayed, raw)
    assert {'old', 'new'}.issubset(set(result.position_id))
    assert 'investment' not in set(result.position_id)
    assert result.current_value_reporting.sum() == raw.current_value_reporting.sum()
    assert result.loc[result.instrument_type.eq('cash'), 'current_value_native'].sum() == 21000.
    assert result.attrs == raw.attrs
    assert raw.loc[raw.position_id.eq('cash'), 'current_value_native'].iloc[0] == 21000.


def test_scoped_investment_risk_excludes_unallocated_cash():
    raw, displayed = invented_views()
    selected = displayed.loc[displayed.position_id.eq('investment')].copy()
    result = investment_risk_positions(selected, raw)
    assert 'cash' not in set(result.position_id)
    assert result.current_value_reporting.sum() == selected.current_value_reporting.sum()
    cash = result.loc[result.instrument_type.eq('cash')].iloc[0]
    assert cash.quote_currency == 'USD'
    assert cash.current_value_native == 20000.
    assert cash.current_value_reporting == 18000.


def test_presentation_keeps_external_debt_and_does_not_restore_fully_attributed_cash():
    raw, displayed = invented_views()
    displayed = displayed.loc[displayed.position_id.eq('investment')].copy()
    displayed.attrs['investment_cash']['investment']['cash'] = {'native': 21000., 'reporting': 18900.}
    displayed.loc[:, 'current_value_reporting'] = 19800.
    capital = pd.DataFrame([
        dict(source_position_id=row.position_id, name=row.name, bucket_id=row.bucket_id,
             value=row.current_value_reporting, kind='owned asset') for row in raw.itertuples()
    ] + [dict(source_position_id='liability:legacy', name='Synthetic debt', bucket_id='', value=-100., kind='liability')])
    exposures = pd.DataFrame([dict(source_position_id='new', bucket_id='equity', exposure_reporting=30000., market_exposure_reporting=30000.)])
    original = PortfolioExposure(capital, exposures, 19800., 100., 0., 19700., (), True)
    result = investment_exposure(original, displayed)
    assert set(result.capital.source_position_id) == {'investment', 'liability:legacy'}
    assert result.capital.value.sum() == original.capital.value.sum()


def test_grouped_investment_normal_overview_and_navigation(tmp_path):
    from portfolio_app.account_journal import InvestmentSpec, save_journal
    from portfolio_app.futures_investments import assign_investment
    from portfolio_app.position_list import list_context
    from portfolio_app.positions import read_snapshot
    from test_actual_portfolio import invented
    from test_accounts_ui import launch_actual
    from test_ui import activate, by_label

    _, journal, _ = invented(tmp_path)
    journal = assign_investment(journal, InvestmentSpec('investment', 'Synthetic investment', 'a', 'EUR', 20000, 20000), ['p'])
    save_journal(tmp_path, journal, None)
    app = launch_actual(tmp_path)
    assert not app.exception
    for measure in ['Market exposure', 'Capital allocation']:
        by_label(app.get('button_group'), 'Allocation measure').set_value(measure).run()
        assert not app.exception
    by_label(app.get('button_group'), 'Overview view').set_value('Performance').run()
    assert not app.exception
    activate(app, 'Positions')
    app.session_state['position_edit_open_request'] = dict(id='investment',
        context=list_context(tmp_path / 'holdings.csv'), revision=read_snapshot(tmp_path / 'holdings.csv').revision, action='edit')
    app.run()
    assert not app.exception
    assert app.session_state['positions_workflow'] == 'Positions'
    assert app.session_state['position_edit_dialog']
    assert len(app.get('dialog')) == 1
