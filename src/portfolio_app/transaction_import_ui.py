"""Reviewed transaction CSV import; calculations and persistence live elsewhere."""
import csv
from datetime import date
from io import StringIO

import pandas as pd
import streamlit as st

from portfolio_app.account_journal import load_journal
from portfolio_app.holdings import DataError
from portfolio_app.import_state import review_control
from portfolio_app.positions import read_snapshot
from portfolio_app.transaction_csv import FIELDS, FIELDS_V2, TransactionRow, digest, read_csv
from portfolio_app.transaction_import_commands import prepare_positions, save_transaction_import
from portfolio_app.transaction_imports import prepare_import


def control(widget, *args, key, **kwargs):
    return review_control(widget, *args, key='import_tx_'+key, **kwargs)


def _uploads():
    def remember():
        upload=st.session_state.get('import_tx_upload')
        st.session_state['import_tx_content']=(upload.name,upload.getvalue()) if upload else None
    upload=st.file_uploader('Transaction CSV',type=['csv'],key='import_tx_upload',on_change=remember)
    if upload:
        st.session_state['import_tx_content']=(upload.name,upload.getvalue())
    return st.session_state.get('import_tx_content')


def _rows(journal):
    mode=control(st.radio,'Evidence source',['Upload CSV','Saved evidence'],horizontal=True,key='input') if journal.transaction_imports else 'Upload CSV'
    if mode=='Saved evidence':
        sources=sorted({r.facts['source_id'] for r in journal.transaction_imports})
        source=control(st.selectbox,'Saved source',sources,key='saved_source')
        superseded={r.replaces for r in journal.transaction_imports if r.replaces}
        return tuple(TransactionRow(r.raw,r.facts,r.issues) for r in journal.transaction_imports if r.facts['source_id']==source and r.id not in superseded)
    layout=control(st.selectbox,'CSV format',['Generic CSV v1','Generic CSV v2','Crypto evidence CSV'],key='layout')
    file=_uploads()
    if file is None:
        fields = FIELDS_V2 if layout == 'Generic CSV v2' else FIELDS
        st.download_button('Download blank CSV template',(','.join(fields)+'\n').encode(),file_name='transaction-template.csv',mime='text/csv')
        return ()
    name,content=file
    st.caption(name)
    source='';assets=()
    if layout=='Crypto evidence CSV':
        source=control(st.text_input,'Source name',key='source',help='A stable name for this ledger. Reuse it on subsequent imports for duplicate protection.')
        raw=list(csv.DictReader(StringIO(content.decode('utf-8-sig'))))
        units=sorted({r.get('fee_currency','') for r in raw}-{''})
        assets=control(st.multiselect,'Fee denominations that are assets',units,key='asset_fees',help='Select asset fees explicitly. Other denominations must be EUR, USD or GBP.')
    return read_csv(content,layout='crypto' if layout=='Crypto evidence CSV' else 'generic',source_id=source,asset_fees=assets)


def _mapping(directory,snapshot,journal,rows,token):
    review_token=token
    pairs=sorted({pair for row in rows if not row.issues for pair in row.pairs})
    assets=sorted({asset for _,asset in pairs})
    holdings=snapshot.holdings
    instruments={r.id:(r['name'],r.get('instrument_type','unknown')) for _,r in holdings.iterrows()}
    retained={(a,asset):pos for r in journal.transaction_imports if r.facts['source_id']==rows[0].facts['source_id'] for a,asset,pos in r.positions}
    existing_books=[b for b in journal.securities if b.position_id in set(retained.values()) and b.custody_id]
    default_mode='By location' if existing_books and not all(b.custody_id.startswith('aggregate:') for b in existing_books) else 'By asset'
    if all(r.facts['event_type'] in ('opening', 'checkpoint') for r in rows):
        default_mode='By location'
    mode=control(st.radio,'Import scope',['By asset','By location'],index=0 if default_mode=='By asset' else 1,horizontal=True,key=token+'scope')
    aggregate=mode=='By asset'
    accounts=tuple(sorted({a for a,_ in pairs} | {a for a,_ in retained})) if aggregate else ()
    token=token+('aggregate_' if aggregate else 'custody_')
    mapping={}; additions=[]; selected_instruments={}
    with st.expander('Map instruments and custody',expanded=True):
        if aggregate:
            st.caption('One reporting position per asset across all reviewed owned locations. Internal principal cancels; fees remain. Existing constituent balances must be resolved before applying.')
        for asset in assets:
            prior=next((pos for (a,symbol),pos in retained.items() if symbol==asset and pos in set(holdings.position_id)),None)
            inferred=holdings.loc[holdings.position_id.eq(prior),'id'].iloc[0] if prior else None
            options=['',*instruments]
            instrument=control(st.selectbox,f'Instrument for {asset}',options,index=options.index(inferred) if inferred else 0,
                format_func=lambda i:'Create a new instrument' if not i else instruments[i][0],key=token+'asset_'+digest(asset)[:12])
            if instrument:
                selected_instruments[asset]=(instrument,*instruments[instrument])
            else:
                name=control(st.text_input,f'Name for {asset}',value=asset,key=token+'name_'+digest(asset)[:12])
                kind=control(st.selectbox,f'Type for {asset}',['unknown','crypto','equity','etf','etc'],key=token+'type_'+digest(asset)[:12])
                identity='csv-asset-'+digest((rows[0].facts['source_id'],asset))[:24]
                selected_instruments[asset]=(identity,name,kind)
        mapped_pairs=[('Combined owned locations',asset) for asset in assets] if aggregate else pairs
        for account,asset in mapped_pairs:
            instrument,name,kind=selected_instruments[asset]
            candidates=holdings.loc[holdings.id.eq(instrument)]
            labels={r.position_id:f"{r['name']} · {r.account or 'No account'}" for _,r in candidates.iterrows()}
            new_id='csv-position-'+digest((rows[0].facts['source_id'],accounts if aggregate else account,asset))[:24]
            prior_positions={p for (a,s),p in retained.items() if s==asset} if aggregate else {retained.get((account,asset))}
            default=(next(iter(prior_positions)) if len(prior_positions)==1 else None) or (new_id if new_id in labels else '')
            options=['',*labels]
            key=token+'position_'+digest((account,asset))[:12]
            # A previously prepared identity is now a selectable persisted row.
            if new_id in labels and st.session_state.get('import_tx_'+key)=='':
                st.session_state['import_tx_'+key]=new_id
            selected=control(st.selectbox,f'{account} / {asset}',options,index=options.index(default) if default in options else 0,
                format_func=lambda i:'Create an empty custody position' if not i else labels[i],key=key)
            if selected:
                mapping[account,asset]=selected
            else:
                mapping[account,asset]=new_id
                additions.append(dict(position_key=new_id,id=instrument,name=name,account=account,instrument_type=kind))
        if aggregate:
            mapping={(a,s):mapping['Combined owned locations',s] for a,s in pairs}
        cash={}
        names={'':'Outside tracked cash',**{a.id:a.name for a in journal.accounts}}
        for account in (() if aggregate else sorted({a for a,_ in pairs})):
            prior_books=[b for b in journal.securities if b.position_id in {mapping[a,s] for a,s in pairs if a==account}]
            previous=next((b.account_id for b in prior_books if b.active),'')
            cash[account]=control(st.selectbox,f'Fiat payments for {account}',list(names),index=list(names).index(previous),
                format_func=names.get,key=token+'cash_'+digest(account)[:12])
        confirmed=control(st.checkbox,'I reviewed instrument identity, owned custody accounts and funding',key=token+'mapping_confirmed')
        if additions:
            st.caption(f'{len(additions)} empty positions to prepare. This records identities, not opening inventory.')
            if st.button('Create missing positions',disabled=not confirmed,key='import_tx_prepare'):
                prepare_positions(directory,additions,expected_holdings_revision=snapshot.revision)
                st.session_state.pop('import_tx_baseline_'+review_token,None)
                st.rerun()
    return mapping,cash,confirmed,not additions,accounts


def render_transaction_import(directory):
    st.subheader('Import transactions')
    from portfolio_app.import_ui import clear_import
    st.button('Cancel transaction import',on_click=clear_import,key='import_tx_cancel')
    try:
        journal,revision=load_journal(directory)
        snapshot=read_snapshot(directory/'holdings.csv')
        if st.session_state.get('import_tx_notice'):
            st.caption(st.session_state['import_tx_notice'])
        rows=_rows(journal)
        if not rows:
            return
        sources={r.facts['source_id'] for r in rows}
        if len(sources)>1:
            raise DataError('Review one source ledger at a time.')
        token=digest([r.identity for r in rows])[:16]+'_'
        from portfolio_app.history_observations import load_observations
        from portfolio_app.portfolio_settings import load_settings
        _, observation_revision = load_observations(directory)
        target = load_settings(directory).reporting_currency
        baseline = (revision,snapshot.revision,observation_revision,target)
        baseline_key = 'import_tx_baseline_'+token
        previous = st.session_state.setdefault(baseline_key,baseline)
        if previous != baseline:
            st.warning('Portfolio or valuation evidence changed. Reload this review before applying transactions.')
            if st.button('Reload transaction review',key='import_tx_reload'):
                for k in list(st.session_state):
                    if k.startswith('import_tx_'+token) or k==baseline_key:
                        del st.session_state[k]
                controls=st.session_state.get('import_controls',{})
                for k in list(controls):
                    if k.startswith('import_tx_'+token):
                        del controls[k]
                st.rerun()
            return
        latest={(r.facts['source_id'],r.facts['event_id']):r for r in journal.transaction_imports}
        active={i for b in journal.securities for i in b.active_ids}
        retained={r.id for r in journal.transaction_imports}
        conflicts=[r for r in rows if r.key in latest and r.identity not in retained]
        entries=[]
        for row in rows:
            f=row.facts
            status='Applied' if row.identity in active else 'Review required' if row.issues or row.incomplete else 'Ready for review'
            entries.append(dict(Event=f['event_id'],Time=f['timestamp_utc'] or 'Unknown',Type=f['event_type'],Account=f['account_id'],
                Asset=f['asset_id'],Quantity=f['quantity'],Status=status,Details='; '.join((*row.issues,*row.incomplete))))
        st.dataframe(pd.DataFrame(entries),hide_index=True,width='stretch')
        with st.expander('Recorded unit movements'):
            from portfolio_app.transaction_evidence import movement_totals
            from portfolio_app.asset_history_ui import render_net_changes
            render_net_changes(rows)
            st.caption('Signed changes only, not balances from an assumed zero. Opening balances and checkpoints '
                'are excluded. Known undated quantities remain undated; unknown asset charges keep the totals incomplete.')
            st.dataframe(pd.DataFrame(movement_totals(rows)).rename(columns={
                'account':'Account','asset':'Asset','known_unit_change':'Known unit change',
                'undated_movements':'Undated movements','unknown_asset_charges':'Unknown asset charges',
                'movements':'Movements'}), hide_index=True, width='stretch')
        corrections=control(st.checkbox,f'Retain {len(conflicts)} changed event(s) as corrections',key=token+'corrections') if conflicts else False
        correction_ids=[r.identity for r in conflicts] if corrections else []
        with st.expander('Movement, valuation and cost policy'):
            st.write('FIFO per custody account and asset. Fiat purchase fees enter cost; sale fees reduce proceeds. '
                'Asset fees consume oldest units first and expense carrying cost. Transfers retain lots. '
                'Swap and reward valuations are supplied monetary evidence, not cash movements. '
                'Blank amounts remain unknown. This is portfolio accounting, not a tax report.')
            st.write('Inventory origin may remain unresolved. A checkpoint replaces units and remaining cost at its '
                'effective date; it does not establish earlier balances or cash flows. Its observation timestamp '
                'may remain unknown. Recorded and estimated monetary evidence retain their source.')
            fields = FIELDS_V2 if any(r.facts['schema_version']=='2' for r in rows) else FIELDS
            stream=StringIO();writer=csv.DictWriter(stream,fieldnames=fields);writer.writeheader();writer.writerows(r.facts for r in rows)
            st.download_button('Download normalized evidence',stream.getvalue(),file_name='transaction-evidence.csv',mime='text/csv')
            evidence = [dict(Event=r.facts['event_id'], **{k:r.facts.get(k,'') for k in
                ('effective_date','acquisition_cost','acquisition_currency','cost_quantity','cost_quality','cost_source',
                 'valuation_amount','valuation_currency','valuation_quality','valuation_source','flow_classification','note')}) for r in rows]
            if any(r.facts['schema_version']=='2' for r in rows):
                st.dataframe(pd.DataFrame(evidence), hide_index=True, width='stretch')
        if st.button('Save evidence only',key='import_tx_save_evidence',disabled=bool(conflicts and not corrections)):
            result=save_transaction_import(directory,rows,{},expected_journal_revision=revision,
                expected_holdings_revision=snapshot.revision,correction_ids=correction_ids)
            st.session_state.pop(baseline_key,None)
            st.session_state['import_tx_notice']=f'{result.added} evidence records saved; balances unchanged.'
            st.rerun()
        from portfolio_app.asset_history_setup_ui import render_asset_history_setup
        render_asset_history_setup(directory, journal, revision, snapshot, rows)
        if all(row.identity in active for row in rows):
            st.caption('These events are already applied. Reimport leaves balances unchanged.')
            from portfolio_app.history_coverage_ui import render_coverage_review
            from portfolio_app.history_observations import HistoryScope
            members={pos for receipt in journal.transaction_imports if receipt.id in {r.identity for r in rows}
                     for _,_,pos in receipt.positions}
            if members:
                render_coverage_review(directory,journal,HistoryScope(tuple(members)),key='import_tx_coverage_')
            return
        mapping,cash,mapping_confirmed,prepared,aggregate_accounts=_mapping(directory,snapshot,journal,rows,token)
        if aggregate_accounts and prepared:
            from portfolio_app.transaction_evidence import compare_asset_changes
            from portfolio_app.security_history import effective_holdings
            current = effective_holdings(snapshot.holdings, journal.securities).set_index('position_id')
            references = {asset: dict(quantity=str(current.loc[pos, 'shares']),
                                     scope=str(current.loc[pos, 'account']), date='')
                          for (_,asset),pos in mapping.items() if pos in current.index}
            with st.expander('Compare recorded net changes'):
                st.dataframe(pd.DataFrame(compare_asset_changes(rows, references, aggregate_accounts)).rename(columns={
                    'asset':'Asset', 'known_unit_change':'Recorded net change', 'reference_quantity':'Recorded holding',
                    'reference_minus_net':'Holding minus net changes', 'reference_date':'Holding observation date',
                    'reference_scope':'Holding scope', 'unknown_asset_charges':'Unknown asset charges'}), hide_index=True, width='stretch')
                st.caption('Arithmetic only: starting inventory, observation date and matching scope are not established. '
                           'A difference is not an opening balance or balancing transaction.')
        eligible={r.identity:r for r in rows if not r.issues}
        default=[i for i,r in eligible.items() if not r.incomplete and r.facts['event_type'] != 'checkpoint']
        selected=control(st.multiselect,'Movements to apply',list(eligible),default=default,
            format_func=lambda i:f"{eligible[i].facts['event_id']} · {eligible[i].facts['event_type']} · {eligible[i].facts['asset_id']}",key=token+'selected')
        zero=control(st.checkbox,'Holdings were zero before the first recorded movement',key=token+'zero',
            help='Required only for positions without a saved opening inventory. Current totals are not an opening purchase.')
        partial=control(st.checkbox,'Apply known movements with missing monetary evidence',key=token+'partial',
            help='Known units can be applied. Missing cost, consideration or fees stay unknown; an unknown asset-fee quantity blocks activation.')
        distinct=control(st.checkbox,'Matching transactions are distinct executions',key=token+'distinct',
            help='Use only after reviewing possible duplicates with different event IDs. Group IDs do not identify duplicates.')
        comparison=control(st.date_input,'Balances compared as of',max_value=date.today(),key=token+'comparison')
        if not selected or not prepared or not mapping_confirmed:
            st.caption('Map positions and select reviewed movements to compare balances.')
            return
        options=dict(cash_accounts=cash,selected_ids=selected,starts_at_zero=zero,allow_incomplete=partial,
                     correction_ids=correction_ids,accept_duplicates=distinct,aggregate_accounts=aggregate_accounts)
        from portfolio_app.history_observations import ObservationFX,load_observations
        from portfolio_app.portfolio_settings import load_settings
        preview=prepare_import(journal,snapshot.holdings,rows,mapping,
            target=load_settings(directory).reporting_currency,historical=ObservationFX(load_observations(directory)[0]),**options)
        st.markdown('**Reconcile balances**')
        if preview.comparisons:
            frame=pd.DataFrame(preview.comparisons).drop(columns=['position_id']).rename(columns={
                'name':'Position','account':'Custody','before_quantity':'Current units','after_quantity':'Reconstructed units',
                'before_cost':'Current cost','after_cost':'Reconstructed cost','currency':'Currency'})
            st.dataframe(frame,hide_index=True,width='stretch')
        if preview.cash_changes:
            st.dataframe(pd.DataFrame(preview.cash_changes),hide_index=True,width='stretch')
        accept_checkpoints = False
        if preview.checkpoints:
            st.markdown('**Balance checkpoints**')
            names = snapshot.holdings.set_index('position_id')['name'].to_dict()
            frame = pd.DataFrame(preview.checkpoints)
            frame['position_id'] = frame.position_id.map(names)
            st.dataframe(frame.rename(columns={'position_id':'Position','effective_date':'Effective from',
                'observed_at':'Observed at','explained_units':'History explains','checkpoint_units':'Accepted units',
                'unresolved_difference':'Unresolved difference'}), hide_index=True, width='stretch')
            st.caption('Unknown means no established earlier balance. Differences are not trades or income. '
                'Effective dates are reporting boundaries, not inferred observation or acquisition dates.')
            accept_checkpoints = control(st.checkbox,'Accept checkpoints and retain unresolved differences',key=token+'checkpoints')
        if preview.warnings:
            st.caption('Partial monetary evidence · '+'; '.join(preview.warnings))
        st.caption(f'{preview.applied} movements to apply · {preview.duplicates} retained records · Comparison {comparison.isoformat()}')
        accepted=control(st.checkbox,'Accept reconstructed balances and shown cash changes',key=token+'accept')
        if st.button('Apply reviewed transactions',type='primary',disabled=not accepted or not preview.applied or bool(preview.checkpoints and not accept_checkpoints),key='import_tx_apply'):
            result=save_transaction_import(directory,rows,mapping,expected_journal_revision=revision,
                expected_holdings_revision=snapshot.revision,accept_reconciliation=True,comparison_date=comparison.isoformat(),
                accept_checkpoints=accept_checkpoints,
                expected_observations_revision=observation_revision,expected_reporting_currency=target,**options)
            st.session_state['import_tx_pending_complete']=result.applied
            st.rerun()
        if journal.securities:
            from portfolio_app.history_coverage_ui import render_coverage_review
            from portfolio_app.history_observations import HistoryScope
            scope=HistoryScope(tuple({p for p in mapping.values() if p in {b.position_id for b in journal.securities}}))
            if scope.positions:
                render_coverage_review(directory,journal,scope,key='import_tx_coverage_',can_confirm=not preview.applied)
    except (DataError,ValueError,UnicodeError,OSError) as exc:
        st.warning(str(exc))
