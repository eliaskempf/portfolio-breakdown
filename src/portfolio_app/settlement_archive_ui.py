"""History reconciliation and background daily-settlement scheduling."""
from datetime import date, timedelta

import pandas as pd
import streamlit as st

from portfolio_app import account_journal as ledger
from portfolio_app.holdings import DataError
from portfolio_app.settlement_archive import SettlementArchive, reconciliation_rows
from portfolio_app.settlement_collection import collector, read_status, scan_dates, weekdays


def settlement_notices(directory, journal):
    if not journal or not journal.positions:
        return [], ()
    try:
        issues = reconciliation_rows(journal,SettlementArchive(directory))
        mismatches = [row for row in issues if row['Status'] != 'No archived price']
        gaps = [(cid,day,row.get('error','')) for cid,record in read_status(directory)['contracts'].items()
                for day,row in record.get('dates',{}).items() if row.get('status') in {'unavailable', 'failed'}]
        messages = ([f'{len(mismatches)} settlement price mismatch(es) · Review settlement history.'] if mismatches else [])
        if gaps:
            messages.append(f'{len(gaps)} settlement date(s) unavailable · See history checks for dates and sources.')
        error = collector.error(directory)
        if error:messages.append('Settlement collection unavailable · '+error)
        identities = tuple(('mismatch',row['identity']) for row in mismatches)+tuple(('gap',*gap) for gap in gaps)
        if error:identities += (('collection-error',error),)
        return messages,identities
    except (DataError,OSError) as exc:
        return ['Settlement history needs attention · '+str(exc)], (('archive-error',str(exc)),)


def _signature(directory):
    return tuple((directory/name).stat().st_mtime_ns if (directory/name).exists() else 0
                 for name in ('settlement-archive.json','settlement-collection.json','futures-quotes.json',ledger.FILE))+(collector.error(directory),)


def watch_settlements(directory, journal, *, demo=False, force=False):
    if demo or not journal or not journal.positions:
        return
    signature = _signature(directory)
    from portfolio_app.futures_quotes import collector as quotes
    collector.schedule(directory,journal,force=force)
    quotes.schedule(directory,journal,force=force)
    quote_error = quotes.error(directory)
    from portfolio_app.futures_clock import utc_now
    minute = int(utc_now().timestamp() // 60)
    @st.fragment(run_every=2 if collector.running(directory) or quotes.running(directory) else 60)
    def watch():
        latest,_ = ledger.load_journal(directory)
        started = collector.schedule(directory,latest)
        quote_started = quotes.schedule(directory,latest)
        if (_signature(directory) != signature or latest != journal or started or quote_started
                or quote_error != quotes.error(directory) or minute != int(utc_now().timestamp() // 60)):
            st.rerun()
    watch()


def render_history_checks(directory,journal,members,*,key):
    archive = SettlementArchive(directory)
    positions={p.id:p for p in journal.positions}
    contracts={c.id:c for c in journal.contracts}
    ids=list(dict.fromkeys(positions[p].contract_id for p in members))
    if not ids:return
    with st.expander('History checks'):
        try:
            selected=st.selectbox('Contract history',ids,
                format_func=lambda cid:f'{contracts[cid].product} · {contracts[cid].expiry}',key=key+'contract')
            contract=contracts[selected]
            yesterday=date.today()-timedelta(days=1)
            left,right=st.columns(2)
            start=left.date_input('Check from',value=yesterday-timedelta(days=6),max_value=yesterday,key=key+'from')
            end=right.date_input('Check through',value=yesterday,max_value=yesterday,key=key+'through')
            if st.button('Check source history',icon=':material/sync:',key=key+'scan',
                help='Re-download up to 31 days and compare with archived and recorded daily settlements. Historical source coverage may be incomplete. New eligible daily settlements update automatically; existing recorded prices need separate corrections.'):
                with st.spinner('Comparing exact-contract history…'):
                    results=scan_dates(directory,contract,weekdays(start.isoformat(),end.isoformat(),contract))
                st.session_state[key+'scan_result']=(selected,start,end,results)
            saved_checks=read_status(directory)['contracts'].get(selected,{}).get('dates',{})
            labels = {'not_published': 'Awaiting publication', 'unavailable': 'Unavailable', 'failed': 'Request failed'}
            unavailable=[{'Date':day,'Source check':labels.get(row['status'], row['status']),'Detail':row.get('error','')}
                         for day,row in sorted(saved_checks.items(),reverse=True) if row['status'] != 'checked']
            if unavailable:
                st.dataframe(pd.DataFrame(unavailable),hide_index=True,width='stretch')
            result=st.session_state.get(key+'scan_result')
            if result and result[:3]==(selected,start,end):
                st.dataframe(pd.DataFrame([{'Date':day,'Source check':row['status'],'Detail':row['error']}
                    for day,row in result[3].items()]),hide_index=True,width='stretch')
            rows=reconciliation_rows(journal,archive,members)
            if rows:
                columns=['Position','Contract','Maturity','Date','Archived price','Retrieved price','Recorded price','Status']
                st.dataframe(pd.DataFrame(rows)[columns],hide_index=True,width='stretch')
                changed=[row for row in rows if 'Source changed' in row['Status']]
                if changed:
                    chosen=st.selectbox('Source change to review',range(len(changed)),key=key+'change',
                        format_func=lambda i:f"{changed[i]['Contract']} {changed[i]['Maturity']} · {changed[i]['Date']}")
                    row=changed[chosen]
                    st.write(f"Archived: {row['Archived price']} → retrieved: {row['Retrieved price']} points")
                    if st.button('Use retrieved price in archive',key=key+'accept',
                        help='Updates the accepted archive price and retains both versions. Existing recorded settlements remain unchanged; correct those separately if needed.'):
                        archive.accept_latest(contracts[row['contract_id']],row['Date'],row['Retrieved price'])
                        st.rerun()
                st.caption('Recorded differences require reviewing the original transaction under Futures → Positions & transactions.')
            else:
                st.caption('No differences found among available archived and recorded prices.')
            dates=archive.entries(contract)
            if dates:
                st.dataframe(pd.DataFrame([{'Date':day,'Archived price':str(observation.price),'Source':observation.source}
                    for day,observation in sorted(dates.items(),reverse=True)]),hide_index=True,width='stretch')
                day=st.selectbox('Observation versions',sorted(dates,reverse=True),key=key+'versions')
                st.dataframe(pd.DataFrame([{'Price':str(o.price),'Source':o.source,'Retrieved at':o.observed_at}
                    for o in archive.versions(contract,day)]),hide_index=True,width='stretch')
        except (DataError,OSError,ValueError) as exc:
            st.error(str(exc))
