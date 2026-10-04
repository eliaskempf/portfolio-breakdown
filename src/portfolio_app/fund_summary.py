"""Dated fund composition summaries; no Streamlit or provider dependencies."""
from datetime import date
import pandas as pd

from portfolio_app.aggregation import classify_exposures, build_tree
from portfolio_app.etf import fund_breakdown


def maturity_band(value: str, as_of: date) -> str:
    try:
        maturity = date.fromisoformat(str(value))
    except (ValueError, TypeError):
        return 'Unknown'
    days = (maturity - as_of).days
    if days < 0:
        return 'Matured / date needs review'
    years = maturity.year - as_of.year - ((maturity.month, maturity.day) < (as_of.month, as_of.day))
    return next((label for limit, label in [(1, 'Under 1 year'), (3, '1–3 years'),
                (5, '3–5 years'), (10, '5–10 years')] if years < limit), '10+ years')


def with_maturity(frame: pd.DataFrame, as_of: date) -> pd.DataFrame:
    result = frame.copy()
    if 'maturity' in result:
        result['maturity_band'] = result.maturity.map(lambda value: maturity_band(value, as_of))
    return result


DIMENSIONS = {'Issuer': 'issuer', 'Country': 'country', 'Denomination currency': 'market_currency',
              'Maturity': 'maturity_band', 'Credit quality': 'credit_rating'}


def composition_summary(fund, dimension: str) -> pd.DataFrame:
    """Whole-fund percentages with explicit unknowns, using the shared taxonomy engine."""
    frame = with_maturity(fund_breakdown(fund), fund.as_of)
    column = DIMENSIONS[dimension]
    labels = frame.get(column, pd.Series('', index=frame.index)).fillna('').astype(str)
    labels = labels.where(~labels.isin(['', '-', 'nan']), 'Unknown')
    labels = labels.mask(frame.constituent_id.str.startswith('etf-other:'), 'Other')
    paths = {row.constituent_id: {'summary': ((label,),)} for row, label in zip(frame.itertuples(), labels, strict=True)}
    exposure = frame.rename(columns={'constituent_id': 'asset_id', 'name': 'asset_name', 'weight': 'value'})
    tree = build_tree(classify_exposures(exposure, paths, 'summary'))
    result = tree.loc[tree.depth.eq(1), ['label', 'value']].rename(columns={'label': dimension, 'value': 'Fund allocation %'})
    result['Fund allocation %'] *= 100
    return result.sort_values('Fund allocation %', ascending=False, ignore_index=True)
