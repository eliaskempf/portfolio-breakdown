"""Scoped target edits with stable identities and revision-checked persistence."""
from dataclasses import dataclass, replace
from io import StringIO
import math

import pandas as pd

from portfolio_app.exposure.exposure_targets import load_exposure_targets, save_exposure_targets, leaf_composition
from portfolio_app.exposure.mixed_exposure_targets import validate_source_membership
from portfolio_app.portfolio.allocation import load_allocation, migration_preview
from portfolio_app.portfolio.category_commands import publish_category_edit
from portfolio_app.portfolio.holdings import DataError
from portfolio_app.portfolio.positions import read_snapshot
from portfolio_app.portfolio.strategic import category_labels
from portfolio_app.workspace.storage import revision
from portfolio_app.workspace.workspace_lock import workspace_lock


@dataclass(frozen=True)
class TargetEdit:
    directory: object
    config: object
    scope: str
    label: str
    requested_scope: str
    market: bool
    policy: object
    revisions: dict
    rows: pd.DataFrame
    holdings: pd.DataFrame
    exposure_result: object = None
    switch_to_members: bool = False
    whole_leaf_split: bool = False

    @property
    def edits_multiple(self):
        return self.market and self.policy is not None and not self.requested_scope


FILES = ('holdings.csv', 'allocation.yaml', 'exposure-targets.json', 'account-journal.json')


def target_holdings(holdings):
    defaults = {'bucket_id':'', 'within_bucket_target':float('nan'), 'target_allocation':float('nan')}
    missing = {column:value for column,value in defaults.items() if column not in holdings}
    return holdings.assign(**missing) if missing else holdings


def capital_target_rows(config, scope, holdings):
    rows = [dict(identity=b.id, kind='category', Investment=b.name, Account='',
                 Target=100*b.target if b.target is not None else float('nan')) for b in config.children(scope)]
    if scope:
        for row in holdings.loc[holdings.bucket_id.eq(scope)].itertuples():
            rows.append(dict(identity=row.position_id, kind='position', Investment=row.name, Account=row.account,
                             Target=100*row.within_bucket_target))
    return rows


def prepare_target_edit(directory, config, scope, *, market=False, exposure_result=None, displayed_positions=None,
                        policy_settings=False, switch_to_members=False, whole_leaf_split=False):
    with workspace_lock(directory):
        files = (*FILES, 'futures-sandbox.json', 'leverage-settings.json') if market else FILES
        stamps = {name: revision(directory/name) for name in files}
        snapshot = read_snapshot(directory/'holdings.csv')
        holdings = target_holdings(snapshot.holdings)
        current = load_allocation(directory/'allocation.yaml', holdings)
        if current is None and (config is None or config == migration_preview(holdings)[0]):
            if market and stamps['exposure-targets.json'] is not None:
                saved, _ = load_exposure_targets(directory, config, validate=False)
                if saved is not None:
                    raise DataError('Review the saved exposure policy under Rebalance → Targets.')
            rows = holdings[['position_id','name','account','target_allocation']].rename(
                columns={'position_id':'identity','name':'Investment','account':'Account','target_allocation':'Target'}).copy()
            rows['Target'] *= 100
            rows['kind'] = 'position'
            return TargetEdit(directory,None,'','Portfolio',scope,False,None,stamps,rows,holdings)
        if current != config:
            raise DataError('This view uses a derived allocation. Review its source under Rebalance → Targets.')
        if scope == 'unassigned':
            raise DataError('Assign these investments under Rebalance → Targets → Category structure.')
        by_id = {b.id:b for b in config.buckets}
        if scope and scope not in by_id:
            raise DataError('The category changed. Reopen the editor.')
        policy, _ = load_exposure_targets(directory, config) if market else (None, None)
        if policy and policy.universe != 'market' and not policy_settings:
            raise DataError('The saved policy targets equity only. Review it under Rebalance → Targets → Exposure targets.')
        if policy and policy.weights == 'mixed':
            validate_source_membership(exposure_result, policy, config)
        requested = scope
        # An aggregate group has one target in its parent, not a member split.
        aggregate = (bool(scope) and (config.flexible(scope) if policy is None else
                     not config.children(scope) and scope not in policy.members))
        if switch_to_members and (policy is not None or not config.flexible(scope)):
            raise DataError('Select a flexible capital category to review its member targets.')
        if whole_leaf_split and (not policy or policy.policy != 'composition' or policy.weights != 'leaves' or scope):
            raise DataError('Whole-portfolio leaf shares require a leaf composition policy.')
        if aggregate and not switch_to_members and (policy is None or policy.policy == 'composition'):
            scope = by_id[scope].parent
        labels = {'':'Portfolio', **category_labels(config)}
        direct = holdings.loc[holdings.bucket_id.eq(scope)] if scope else holdings.iloc[:0]
        records = []
        if policy is None:
            if displayed_positions is not None and scope:
                visible = set(displayed_positions.loc[displayed_positions.bucket_id.eq(scope),'position_id'])
                if not visible <= set(direct.position_id):
                    raise DataError('This category includes separately managed positions. Review their source under Rebalance → Targets.')
            records = capital_target_rows(config, scope, holdings)
        elif whole_leaf_split:
            for leaf in sorted(config.leaves(), key=labels.get):
                records.append(dict(identity=leaf, kind='category', Investment=labels[leaf], Account='',
                                    Target=policy.composition.get(leaf, 0.)*100))
        elif policy.policy == 'overlay_only':
            if requested:
                raise DataError('Funded weights. Select Portfolio to edit total exposure.')
        else:
            children = config.children(scope)
            global_weights = leaf_composition(policy, config) if policy.weights == 'leaves' else {}
            scope_weight = sum(global_weights.get(key,0.) for key in config.leaves(scope)) if global_weights else 1.
            if global_weights and scope_weight <= 0:
                raise DataError('Zero exposure budget. Choose Edit whole-portfolio leaf shares in Policy settings.')
            for child in children:
                target = (sum(global_weights.get(key,0.) for key in config.leaves(child.id))/scope_weight
                    if policy.weights == 'leaves' else policy.composition[child.id])
                records.append(dict(identity=child.id, kind='category', Investment=child.name, Account='',
                                    Target=target*100 if target is not None else float('nan')))
            if policy.weights == 'mixed':
                sources = exposure_result.capital.set_index('source_position_id')
                accounts = exposure_result.exposures.drop_duplicates('source_position_id').set_index('source_position_id').get('account',pd.Series(dtype=str))
                for pid, fraction in policy.members.get(scope,{}).items():
                    records.append(dict(identity=pid,kind='position',Investment=sources.loc[pid,'name'],Account=accounts.get(pid,''),Target=fraction*100))
            elif len(direct) and children:
                raise DataError('Review the mixed allocation split under Rebalance → Targets → Exposure targets → Policy settings.')
        frame = pd.DataFrame(records,columns=['identity','kind','Investment','Account','Target'])
        frame['Target'] = pd.to_numeric(frame.Target)
        return TargetEdit(directory,config,scope,labels[scope],requested,market,policy,stamps,frame,holdings,exposure_result,
                          switch_to_members,whole_leaf_split)


def save_target_edit(draft, edited, *, multiple=None, allow_partial=False):
    expected = set(zip(draft.rows.kind,draft.rows.identity))
    if len(edited) != len(draft.rows) or set(zip(edited.kind,edited.identity)) != expected:
        raise DataError('The target rows changed. Reopen the editor.')
    if allow_partial and (draft.policy is not None or draft.switch_to_members):
        raise DataError('This target split must total 100% before saving.')
    if len(edited):
        values = pd.to_numeric(edited.Target,errors='coerce')
        missing = values.isna()
        invalid = (edited.Target.notna() & missing).any() or not values.dropna().between(0,100).all()
        if invalid or (missing.any() and not allow_partial):
            raise DataError('Enter a target from 0% to 100% for every row.')
        if not allow_partial and not math.isclose(float(values.sum()),100.,abs_tol=1e-7):
            raise DataError('Targets must total 100% before saving.')
    elif not draft.edits_multiple:
        raise DataError('There are no targets to edit in this category.')
    if draft.edits_multiple and (multiple is None or not math.isfinite(multiple) or multiple < 0):
        raise DataError('Enter a nonnegative total exposure target.')
    with workspace_lock(draft.directory):
        if any(revision(draft.directory/name) != stamp for name,stamp in draft.revisions.items()):
            raise DataError('Portfolio changed while editing. Reopen the editor to review the latest targets.')
        if draft.config is None:
            from portfolio_app.portfolio.holdings import parse_holdings
            from portfolio_app.workspace.storage import save_document
            path = draft.directory/'holdings.csv'
            raw = pd.read_csv(StringIO(path.read_text(encoding='utf-8-sig')),dtype=str,keep_default_na=False)
            targets = edited.set_index('identity').Target
            fractions = draft.holdings.position_id.map(targets) / 100
            raw['target_allocation'] = fractions.where(fractions.notna(), '').to_numpy()
            content = raw.to_csv(index=False)
            parse_holdings(content)
            save_document(path, content, draft.revisions['holdings.csv'])
        elif draft.policy is None:
            buckets = {b.id:b for b in draft.config.buckets}
            raw = pd.read_csv(StringIO((draft.directory/'holdings.csv').read_text(encoding='utf-8-sig')),dtype=str,keep_default_na=False)
            for row in edited.itertuples():
                fraction = None if pd.isna(row.Target) else float(row.Target)/100
                if row.kind == 'category':
                    buckets[row.identity] = replace(buckets[row.identity],target=fraction)
                else:
                    indexes = draft.holdings.index[draft.holdings.position_id.eq(row.identity)]
                    raw.loc[indexes,'within_bucket_target'] = '' if fraction is None else str(fraction)
            if draft.switch_to_members:
                buckets[draft.scope] = replace(buckets[draft.scope], allocation_mode='targets', buy_priority=(), sell_priority=())
            publish_category_edit(draft.directory,replace(draft.config,buckets=tuple(buckets.values())),raw.to_csv(index=False),
                holdings_revision=draft.revisions['holdings.csv'],allocation_revision=draft.revisions['allocation.yaml'])
        else:
            policy = draft.policy
            composition = dict(policy.composition)
            members = {owner:dict(weights) for owner,weights in policy.members.items()}
            scope_weight = sum(composition.get(key,0.) for key in draft.config.leaves(draft.scope)) if policy.weights == 'leaves' else 1.
            for row in edited.itertuples():
                fraction = float(row.Target)/100
                if draft.whole_leaf_split:
                    composition[row.identity] = fraction
                elif row.kind == 'position':
                    members[draft.scope][row.identity] = fraction
                elif policy.weights == 'leaves':
                    leaves = draft.config.leaves(row.identity)
                    previous = sum(policy.composition.get(key,0.) for key in leaves)
                    if previous == 0 and fraction > 0 and len(leaves) > 1:
                        raise DataError('Set the zero-budget branch under Rebalance → Targets → Exposure targets → Policy settings → Edit whole-portfolio leaf shares.')
                    for leaf in leaves:
                        composition[leaf] = scope_weight*fraction*(policy.composition.get(leaf,0.)/previous if previous else 1. if len(leaves)==1 else 0.)
                else:
                    composition[row.identity] = fraction
            candidate = replace(policy,composition=composition,members=members,
                multiple=float(multiple) if draft.edits_multiple else policy.multiple)
            if candidate.weights == 'mixed':
                validate_source_membership(draft.exposure_result,candidate,draft.config)
            save_exposure_targets(draft.directory,candidate,draft.config,draft.revisions['exposure-targets.json'])
