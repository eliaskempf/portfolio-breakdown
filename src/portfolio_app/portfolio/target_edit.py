"""Scoped target edits with stable identities and revision-checked persistence."""
from dataclasses import dataclass, replace
from io import StringIO
import math

import pandas as pd

from portfolio_app.exposure.exposure_targets import load_exposure_targets, save_exposure_targets, leaf_composition
from portfolio_app.exposure.mixed_exposure_targets import validate_source_membership
from portfolio_app.portfolio.allocation import load_allocation
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

    @property
    def edits_multiple(self):
        return self.market and self.policy is not None and not self.requested_scope


FILES = ('holdings.csv', 'allocation.yaml', 'exposure-targets.json', 'account-journal.json')


def prepare_target_edit(directory, config, scope, *, market=False, exposure_result=None, displayed_positions=None):
    with workspace_lock(directory):
        stamps = {name: revision(directory/name) for name in FILES}
        snapshot = read_snapshot(directory/'holdings.csv')
        current = load_allocation(directory/'allocation.yaml', snapshot.holdings)
        if current != config:
            raise DataError('This view uses a derived allocation. Use Manage targets to edit its source targets.')
        if scope == 'unassigned':
            raise DataError('Assign these investments to a category in Manage targets first.')
        by_id = {b.id:b for b in config.buckets}
        if scope and scope not in by_id:
            raise DataError('The category changed. Reopen the editor.')
        policy, _ = load_exposure_targets(directory, config) if market else (None, None)
        if policy and policy.universe != 'market':
            raise DataError('The saved policy targets equity only. Use Manage targets to review its exposure universe.')
        if policy and policy.weights == 'mixed':
            validate_source_membership(exposure_result, policy, config)
        requested = scope
        # An aggregate group has one target in its parent, not a member split.
        aggregate = (bool(scope) and (config.flexible(scope) if policy is None else
                     not config.children(scope) and scope not in policy.members))
        if aggregate and (policy is None or policy.policy == 'composition'):
            scope = by_id[scope].parent
        labels = {'':'Portfolio', **category_labels(config)}
        direct = snapshot.holdings.loc[snapshot.holdings.bucket_id.eq(scope)] if scope else snapshot.holdings.iloc[:0]
        records = []
        if policy and policy.policy == 'overlay_only':
            if requested:
                raise DataError('This policy follows funded holdings. Use Manage targets to change its setup, or edit total exposure at Portfolio.')
        else:
            children = config.children(scope)
            global_weights = leaf_composition(policy, config) if policy and policy.weights == 'leaves' else {}
            scope_weight = sum(global_weights.get(key,0.) for key in config.leaves(scope)) if global_weights else 1.
            if global_weights and scope_weight <= 0:
                raise DataError('This category has no assigned exposure budget. Set its split in Manage targets first.')
            for child in children:
                target = child.target if policy is None else (sum(global_weights.get(key,0.) for key in config.leaves(child.id))/scope_weight
                    if policy.weights == 'leaves' else policy.composition[child.id])
                records.append(dict(identity=child.id, kind='category', Investment=child.name, Account='',
                                    Target=target*100 if target is not None else float('nan')))
            if policy is None:
                if displayed_positions is not None and scope:
                    visible = set(displayed_positions.loc[displayed_positions.bucket_id.eq(scope),'position_id'])
                    if not visible <= set(direct.position_id):
                        raise DataError('This category includes separately managed positions. Use Manage targets to edit their allocation.')
                for row in direct.itertuples():
                    records.append(dict(identity=row.position_id,kind='position',Investment=row.name,Account=row.account,Target=row.within_bucket_target*100))
            elif policy.weights == 'mixed':
                sources = exposure_result.capital.set_index('source_position_id')
                accounts = exposure_result.exposures.drop_duplicates('source_position_id').set_index('source_position_id').get('account',pd.Series(dtype=str))
                for pid, fraction in policy.members.get(scope,{}).items():
                    records.append(dict(identity=pid,kind='position',Investment=sources.loc[pid,'name'],Account=accounts.get(pid,''),Target=fraction*100))
            elif len(direct) and children:
                raise DataError('This saved policy needs a mixed allocation split. Use Manage targets to review it.')
        frame = pd.DataFrame(records,columns=['identity','kind','Investment','Account','Target'])
        frame['Target'] = pd.to_numeric(frame.Target)
        return TargetEdit(directory,config,scope,labels[scope],requested,market,policy,stamps,frame,snapshot.holdings,exposure_result)


def save_target_edit(draft, edited, *, multiple=None):
    expected = set(zip(draft.rows.kind,draft.rows.identity))
    if len(edited) != len(draft.rows) or set(zip(edited.kind,edited.identity)) != expected:
        raise DataError('The target rows changed. Reopen the editor.')
    if len(edited):
        values = pd.to_numeric(edited.Target,errors='coerce')
        if any(not math.isfinite(v) or not 0 <= v <= 100 for v in values):
            raise DataError('Enter a target from 0% to 100% for every row.')
        if not math.isclose(float(values.sum()),100.,abs_tol=1e-7):
            raise DataError('Targets must total 100% before saving.')
    elif not draft.edits_multiple:
        raise DataError('There are no targets to edit in this category.')
    if draft.edits_multiple and (multiple is None or not math.isfinite(multiple) or multiple < 0):
        raise DataError('Enter a nonnegative total exposure target.')
    with workspace_lock(draft.directory):
        if any(revision(draft.directory/name) != stamp for name,stamp in draft.revisions.items()):
            raise DataError('Portfolio changed while editing. Reopen the editor to review the latest targets.')
        if draft.policy is None:
            buckets = {b.id:b for b in draft.config.buckets}
            raw = pd.read_csv(StringIO((draft.directory/'holdings.csv').read_text(encoding='utf-8-sig')),dtype=str,keep_default_na=False)
            for row in edited.itertuples():
                if row.kind == 'category':
                    buckets[row.identity] = replace(buckets[row.identity],target=float(row.Target)/100)
                else:
                    indexes = draft.holdings.index[draft.holdings.position_id.eq(row.identity)]
                    raw.loc[indexes,'within_bucket_target'] = str(float(row.Target)/100)
            publish_category_edit(draft.directory,replace(draft.config,buckets=tuple(buckets.values())),raw.to_csv(index=False),
                holdings_revision=draft.revisions['holdings.csv'],allocation_revision=draft.revisions['allocation.yaml'])
        else:
            policy = draft.policy
            composition = dict(policy.composition)
            members = {owner:dict(weights) for owner,weights in policy.members.items()}
            scope_weight = sum(composition.get(key,0.) for key in draft.config.leaves(draft.scope)) if policy.weights == 'leaves' else 1.
            for row in edited.itertuples():
                fraction = float(row.Target)/100
                if row.kind == 'position':
                    members[draft.scope][row.identity] = fraction
                elif policy.weights == 'leaves':
                    leaves = draft.config.leaves(row.identity)
                    previous = sum(policy.composition.get(key,0.) for key in leaves)
                    if previous == 0 and fraction > 0 and len(leaves) > 1:
                        raise DataError('Set the internal split of the zero-target category in Manage targets first.')
                    for leaf in leaves:
                        composition[leaf] = scope_weight*fraction*(policy.composition.get(leaf,0.)/previous if previous else 1. if len(leaves)==1 else 0.)
                else:
                    composition[row.identity] = fraction
            candidate = replace(policy,composition=composition,members=members,
                multiple=float(multiple) if draft.edits_multiple else policy.multiple)
            if candidate.weights == 'mixed':
                validate_source_membership(draft.exposure_result,candidate,draft.config)
            save_exposure_targets(draft.directory,candidate,draft.config,draft.revisions['exposure-targets.json'])
