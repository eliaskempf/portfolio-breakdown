"""Concise help shared by native widget help and futures-entry label hover."""
import json
import streamlit as st

HELP = {
    'Record': 'New opening trade records an executed trade. An already-open snapshot starts from its latest settled price and cash balance; earlier P&L stays unknown.',
    'Futures product': 'Choose the index future and contract size. Product codes identify a family; the exact maturity identifies the contract.',
    'Trade / snapshot date': 'Actual execution date for a new trade, or the date of the cash balance and clearing basis used for a snapshot.',
    'Exact contract maturity': 'Choose the expiry of your actual contract. Historical / manual entry is available for expired or unlisted contracts.',
    'Manual maturity': 'Exact maturity from your contract or trade confirmation. This date is not verified against the exchange listing.',
    'Product code': 'The exchange product code from your contract specification.',
    'Settlement currency': 'Currency in which this contract settles its gains, losses and fees.',
    'Currency per index point': 'Cash value of a one-point price move for one contract. For example, 100 means one point changes P&L by 100 in the settlement currency.',
    'Price tick': 'Minimum quoted price increment from the contract specification.',
    'Signed contracts (negative for short)': 'Whole contracts held: positive for a long position, negative for a short position. This is not the cash amount invested.',
    'Fill price': 'Actual executed futures price in index points, not the underlying index level or contract notional.',
    'Last settled price / clearing basis': 'The last price through which gains or losses are already reflected in the snapshot cash balance. Earlier settlements must not be entered again.',
    'Opening fees': 'Total fees for this opening trade, in the settlement currency. Enter the total once, not a per-contract fee.',
    'New fees to record (exclude costs already in opening cash)': 'Only additional fees not already reflected in the snapshot cash balance, in the settlement currency.',
    'Account': 'The cash account supporting this investment, usually your broker account. One account can support several futures investments. No broker connection is made.',
    'Account name': 'A label for the cash account, such as your broker name or Futures account. It can be shared by several positions.',
    'Opening cash currency': 'Currency of the opening cash balance. Other currency balances can be recorded later under Futures → Cash & financing.',
    'Cash source': 'Adopt existing cash if it already appears in your holdings; its value is counted only once. Otherwise enter cash not already recorded elsewhere.',
    'Cash balance (cash only, not account net liquidation value)': 'Actual cash in this account on the entry date, including restricted cash. Exclude securities and contract notional. For a snapshot, earlier settlements are already included. Enter zero if no cash is being recorded.',
    'Investment name': 'Name shown in your portfolio for this futures investment. It stays with the investment through contract rolls.',
    'Initial allocated capital': 'Part of existing account cash assigned to this investment; no new deposit. This is the fixed return denominator. With 30,000 cash and 20,000 allocated, 10,000 remains unallocated. Not contract notional or margin.',
    'Category': 'Asset category used for this investment in allocation and exposure targets.',
    'Index holdings proxy (optional)': 'Choose an ETF tracking the same underlying index to estimate company, country and sector weights. Its holdings are used only for exposure breakdowns, not futures prices or P&L. No proxy leaves that detailed breakdown unavailable.',
}


def field_help(label):
    return HELP.get('Initial allocated capital' if label.startswith('Initial allocated capital (') else label)


def install_label_help():
    # Native help remains keyboard-accessible. Label titles also support direct
    # hover, scoped to this dialog and static descriptions rather than user data.
    st.html('''<script>(() => {
      window.__futureLabelHelp?.disconnect();
      const help = FIELD_HELP;
      const root = document.querySelector('[role="dialog"]');
      if (!root) return;
      const update = () => root.querySelectorAll('[data-testid="stWidgetLabel"]').forEach(label => {
        const name = label.querySelector('p')?.textContent.trim();
        const text = help[name?.startsWith('Initial allocated capital (') ? 'Initial allocated capital' : name];
        if (text) label.title = text;
      });
      update();
      const observer = new MutationObserver(update);
      observer.observe(root, {childList:true, subtree:true});
      window.__futureLabelHelp = observer;
    })();</script>'''.replace('FIELD_HELP',json.dumps(HELP)), unsafe_allow_javascript=True)
