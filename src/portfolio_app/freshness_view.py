"""Age a displayed valuation snapshot; never fetch data or change its amounts."""
import pandas as pd

from portfolio_app.futures_valuation import valuation_fields, summarize_freshness


def has_live_freshness(rows):
    if rows is None:
        return False
    identities = set(rows.position_id)
    for identity, members in rows.attrs.get('investment_members', {}).items():
        if identity in identities:
            identities.update(members)
    return any(identity in identities and not context.closed and context.observation is not None
               and (context.observation.quote_at or context.observation.kind == 'daily_settlement')
               for identity, context in rows.attrs.get('futures_freshness', {}).items())


def refresh_freshness(rows, *, now=None):
    contexts = rows.attrs.get('futures_freshness', {})
    if not contexts:
        return rows
    if now is None:
        from portfolio_app.futures_valuation import utc_now
        now = utc_now()
    fields = {identity: valuation_fields(context.at(now)) for identity, context in contexts.items()}
    members = rows.attrs.get('investment_members', {})
    result = rows.copy()
    for index, row in result.iterrows():
        identity = row.position_id
        if identity in members:
            active = [fields[pid] for pid in members[identity]
                      if pid in fields and not contexts[pid].closed]
            if len(active) == 1:
                updated = active[0]
            else:
                names = rows.attrs.get('futures_freshness_names', {})
                named = [dict(value, name=names.get(pid, 'Contract')) for pid, value in fields.items()
                         if pid in members[identity] and not contexts[pid].closed]
                label, detail, qualified = summarize_freshness(pd.DataFrame(named))
                updated = dict(valuation_status='mixed' if active else 'closed',
                    valuation_label=label if active else 'Closed',
                    valuation_detail=detail, valuation_qualified=qualified)
        else:
            updated = fields.get(identity, {})
        for key, value in updated.items():
            result.at[index, key] = value
    return result
