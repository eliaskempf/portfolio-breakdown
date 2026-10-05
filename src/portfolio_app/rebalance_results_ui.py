"""Shared result layout for portfolio and category plans."""

from portfolio_app.currency_display import currency_symbol
import streamlit as st

from portfolio_app.list_ui import ListColumn, frame_rows, render_list
from portfolio_app.rebalance_tables import portfolio_impact, suggested_trades
from portfolio_app.strategic import category_labels


def show_table(table, *, scope='portfolio', cap_scope='portfolio'):
    config = {column: st.column_config.NumberColumn(format=('€ %.2f').replace('€', currency_symbol())) for column in table if column in {'Trade', 'Current', 'After', 'Reserved', 'Invested', 'Unallocated', 'Budget', 'Value', 'Known subtotal'}}
    config.update({column: st.column_config.NumberColumn(
        f'{"Planned" if column == "After %" else column.removesuffix(" %")} (% of {scope})', format='%.2f %%')
        for column in table if column.endswith(' %')})
    config.update({'After': st.column_config.NumberColumn('Planned value', format=('€ %.2f').replace('€', currency_symbol())),
                   'Current': st.column_config.NumberColumn('Current value', format=('€ %.2f').replace('€', currency_symbol())),
                   'Trade': st.column_config.NumberColumn('Amount', format=('€ %.2f').replace('€', currency_symbol())),
                   'Max allocation %': st.column_config.NumberColumn(f'Maximum (% of {cap_scope})', format='%.2f %%'),
                   'Gap (pp)': st.column_config.NumberColumn('Planned − target (pp)', format='%+.2f')})
    st.dataframe(table, hide_index=True, height='content', width='stretch', column_config=config)


def render_summary(table, amount, cash, *, minimum=False):
    columns = st.columns(4)
    trades = table['Trade']
    columns[0].metric('Trades', int(trades.ne(0).sum()),
                      help=f'{int(trades.gt(0).sum())} buys · {int(trades.lt(0).sum())} sells')
    columns[1].metric('Minimum new money' if minimum else 'New money', (f'€{amount:,.2f}').replace('€', currency_symbol()))
    columns[2].metric('Invested contribution', (f'€{amount - cash:,.2f}').replace('€', currency_symbol()),
                      help='New money invested, excluding sale proceeds reinvested within the plan.')
    columns[3].metric('Unallocated cash', (f'€{cash:,.2f}').replace('€', currency_symbol()),
                      help='Included in the final planning value. This preview does not create a saved cash holding.')
    if cash:
        st.info('Some contribution remains unallocated under the purchase rules. It is included in the final planning value.')


def render_trades(table):
    st.subheader('Suggested trades')
    trades = suggested_trades(table)
    if trades.empty:
        st.info('No trades in this plan.')
    else:
        labels = {'Trade': 'Amount', 'Current': 'Current value',
                  'After': 'Planned value'}
        columns = [ListColumn(column, labels.get(column, column),
                              numeric=column in {'Trade', 'Current', 'After', 'Reserved', 'Invested', 'Unallocated', 'Budget', 'Value', 'Known subtotal'},
                              prefix=('€ ').replace('€', currency_symbol()) if column in {'Trade', 'Current', 'After', 'Reserved', 'Invested', 'Unallocated', 'Budget', 'Value', 'Known subtotal'} else '', color_signed=False)
                   for column in trades]
        render_list(frame_rows(trades), columns, key='rebalance_trades_list',
                    context=f"trades:{st.session_state.get('portfolio_workspace_context', '')}",
                    title='Suggested trades')


def render_impact(before, after, allocation, *, cash=0., key):
    with st.expander('Portfolio impact'):
        names = {'': 'Portfolio', **category_labels(allocation)}
        parents = ['', *[b.id for b in allocation.buckets if allocation.children(b.id)]]
        if st.session_state.get(key, '') not in parents:
            st.session_state[key] = ''
        parent = st.selectbox('Compare categories within', parents, format_func=names.get, key=key)
        st.caption(f'Percentages of {names[parent]}. Planned values include this plan’s trades.' +
                   (' Unallocated contribution is included in the portfolio total.' if cash and not parent else ''))
        show_table(portfolio_impact(before, after, allocation, extra_cash=cash, parent=parent), scope=names[parent])
