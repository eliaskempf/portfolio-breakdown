"""Distinguish assets without a business sector from missing sector metadata."""
from collections import defaultdict

from portfolio_app.etf import constituent_resolver, matching_fund

NO_SECTOR = 'No business sector'


def sector_classifications(classifications, holdings, funds):
    """Add analytical fallbacks without modifying saved or provider classifications.

    Only explicit instrument/asset-class evidence qualifies. Bonds, unknown ETFs
    and uncovered fund weights remain unclassified unless they have sector data.
    Substitute baskets never supply economic sector exposure.
    """
    result = {asset: dict(entry) for asset, entry in classifications.items()}
    candidates = defaultdict(set)

    def observe(asset, row):
        kind = row.get('instrument_type')
        label = {'cash': 'Cash', 'crypto': 'Crypto', 'overnight_rate': 'Money market'}.get(kind)
        if row.get('isin') == 'DE000EWG2LD7' or (kind == 'physical' and row.get('price_source') == 'gold_spot'):
            label = 'Gold'
        if label:
            candidates[asset].add(label)

    for row in holdings.to_dict('records'):
        asset = row.get('analysis_asset_id')
        asset = asset if isinstance(asset, str) and asset else row['id']
        observe(asset, row)
        fund = matching_fund(row, funds)
        if (fund is not None and fund.breakdown_basis == 'economic' and fund.asset_class == 'money_market'
                and not fund.constituents.empty and 'instrument_type' in fund.constituents
                and fund.constituents.instrument_type.eq('overnight_rate').all()
                and abs(fund.constituents.weight.sum() - 1.) <= 1e-12):
            candidates[asset].add('Money market')
    resolve = constituent_resolver(holdings)
    for fund in funds:
        for row in fund.constituents.to_dict('records'):
            observe(resolve(row)[0], row)
    for asset, entry in classifications.items():
        for path in entry.get('asset_class', ()):
            if path[-1].casefold() == 'gold':
                candidates[asset].add('Gold')
            elif path[0].casefold() in {'money market', 'cash', 'crypto'}:
                candidates[asset].add({'money market': 'Money market', 'cash': 'Cash', 'crypto': 'Crypto'}[path[0].casefold()])
    for asset, labels in candidates.items():
        if len(labels) == 1 and not result.get(asset, {}).get('sector'):
            result.setdefault(asset, {})['sector'] = ((NO_SECTOR, next(iter(labels))),)
    return result
