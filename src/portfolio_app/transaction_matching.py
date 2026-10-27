"""Source-independent matching of CSV asset identifiers and public listings."""
from portfolio_app.instruments import normalized_query, valid_isin


def holding_label(row):
    return ' · '.join(str(value).strip() for value in (row['name'], row['account']) if str(value).strip())


def listing_label(listing):
    return listing.ticker+' · '+instrument_fields(listing, listing.ticker)['name']


def listing_currency(listing):
    if listing.currency:
        return listing.currency
    if listing.kind == 'CRYPTOCURRENCY' and '-' in listing.ticker:
        return listing.ticker.rsplit('-', 1)[1]
    return ''


def holding_matches(asset, holdings):
    query = normalized_query(asset)
    matches = []
    for _, row in holdings.iterrows():
        identifiers = {normalized_query(row.get(k, '')) for k in ('id', 'name', 'ticker', 'isin')}
        if row.get('instrument_type') == 'crypto' and '-' in row.get('ticker', ''):
            identifiers.add(normalized_query(row.ticker.rsplit('-', 1)[0]))
        if query and query in identifiers:
            matches.append(row.position_id)
    return matches


def exact_listings(asset, listings, *, kind=''):
    query = normalized_query(asset)
    result = []
    for listing in listings:
        if kind and listing.kind != kind:
            continue
        identifiers = {normalized_query(listing.ticker), normalized_query(listing.name)}
        if listing.isin:
            identifiers.add(normalized_query(listing.isin))
        if listing.kind == 'CRYPTOCURRENCY':
            identifiers.add(normalized_query(listing.ticker.rsplit('-', 1)[0]))
            identifiers.add(normalized_query(listing.name.removesuffix(' '+listing_currency(listing))))
        if query in identifiers:
            result.append(listing)
    return result


def suggested_listing(asset, listings, currency, *, kind=''):
    """Suggest only one identified instrument; currency variants are not assets."""
    exact = exact_listings(asset, listings, kind=kind)
    identities = {('crypto', x.ticker.rsplit('-', 1)[0]) if x.kind == 'CRYPTOCURRENCY'
                  else ('isin', valid_isin(x.isin)) if valid_isin(x.isin) else ('ticker', x.ticker)
                  for x in exact}
    if len(identities) != 1:
        return None
    return min(exact, key=lambda x: (listing_currency(x) != currency, listing_currency(x) != 'USD', x.ticker))


def instrument_fields(listing, asset):
    if listing is None:
        return dict(name=asset, instrument_type='unknown', ticker='', isin='')
    name = listing.name.removesuffix(' '+listing_currency(listing)) if listing.kind == 'CRYPTOCURRENCY' else listing.name
    return dict(name=name, ticker=listing.ticker, isin=listing.isin,
                instrument_type={'EQUITY':'equity', 'ETF':'etf', 'ETC':'etc', 'CRYPTOCURRENCY':'crypto'}.get(listing.kind, 'unknown'))
