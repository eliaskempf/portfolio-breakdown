"""The guided demo's route and copy, independent of rendering."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TourStep:
    tab: str
    title: str
    summary: str
    anchors: tuple[str, ...]
    view: str = ''
    lookthrough: bool = True
    calculate_plan: bool = False
    interactive: str = ''
    detached_anchors: tuple[str, ...] = ()
    target_scope: str = ''
    dated_portfolio: bool = False

    @property
    def highlight_groups(self) -> tuple[tuple[str, ...], ...]:
        return tuple((anchor,) for anchor in self.detached_anchors) + (self.anchors,)


STEPS = (
    TourStep('Overview', 'Explore your portfolio',
        'Start in Overview to see your holdings, allocation and performance. Exposure looks inside funds. Positions opens your records. Rebalance, Monitoring and Taxes have their own tabs.',
        ('.st-key-main_tabs > div > [role="tablist"]',)),
    TourStep('Overview', 'Choose the portfolio you want to examine',
        'Choose Portfolio or a category. The allocation, current value and portfolio share follow that choice.',
        ('.st-key-tour_overview_scope',)),
    TourStep('Overview', 'See where your money is allocated',
        'The sunburst and table show the same allocation. Try clicking a chart category to explore it, then click the center to go back. The table follows your selection and compares current weights with targets.',
        ('.st-key-strategic_view', '.st-key-overview_allocation'), 'Allocation', interactive='.st-key-overview_allocation .js-plotly-plot'),
    TourStep('Overview', 'Read your personal performance',
        'Try a different period. Gain includes recorded interest, income and realized gains. Click the gain beside Portfolio value to see time-weighted return. Annualized return accounts for the timing of contributions.',
        ('.st-key-tour_history_period',), 'Performance', interactive='.st-key-tour_history_period'),
    TourStep('Overview', 'See what you owned on a date',
        'From Performance, choose View holdings on a date. This portfolio shows recorded holdings and cash at historical closing values. The date and coverage tell you which figures you can compare.',
        ('.st-key-historical_portfolio',), 'Performance', dated_portfolio=True),
    TourStep('Overview', 'Understand risk and diversification',
        'Calculate risk estimates volatility, sensitivity to a benchmark, and correlation. We have calculated this example using synthetic history. It describes today’s allocation, not your personal historical return.',
        ('.st-key-strategic_view', '.st-key-tour_risk_controls', '.st-key-tour_risk_metrics'), 'Analytics'),
    TourStep('Exposure', 'Look inside your ETFs',
        'Break down ETFs replaces supported funds with their underlying holdings. Here it is off, so you see the securities held directly. Next, we will turn it on.',
        ('.st-key-tour_etf_breakdown',), 'Assets', lookthrough=False),
    TourStep('Exposure', 'Discover your underlying exposure',
        'The table now includes ETF constituents, combined with any direct holdings in the same asset. In a portfolio with futures, market exposure also includes their signed notional and identifies proxy contributions in Sources. XEON becomes one EUR overnight-rate exposure, not its collateral basket. Uncovered ETF weight stays visible as Other; unsupported funds remain whole.',
        ('.st-key-exposure_view', '.st-key-tour_exposure_assets'), 'Assets', detached_anchors=('.st-key-tour_etf_breakdown',)),
    TourStep('Exposure', 'Explore themes and sectors',
        'Group the underlying exposure by sector. Money market, gold and crypto sit under No business sector; Unclassified means missing data. Explore a category to see the underlying assets.',
        ('.st-key-exposure_view', '.st-key-exposure_group', '.st-key-exposure_theme_results'), 'Themes & sectors'),
    TourStep('Exposure', 'See your geographic exposure',
        'Compare regions or countries, then choose an area to explore its detail. Unknown geography remains explicit rather than being assigned to a country.',
        ('.st-key-exposure_view', '.st-key-tour_geography'), 'Geography'),
    TourStep('Positions', 'Trace a holding to its trades',
        'Open the World ETF row. History → Holdings shows your investment over time and Price shows the instrument. Open Transactions to inspect the purchases and sale, then close the detail to continue.',
        ('.st-key-tour_position_list',), 'Transactions', interactive='.st-key-tour_position_list'),
    TourStep('Positions', 'Follow interest into the cash balance',
        'Open Emergency fund to inspect its statement. Deposits increase contributions. Posted interest increases gain. Close the account to return to the tour.',
        ('.st-key-tour_position_list',), 'Cash account', interactive='.st-key-tour_position_list'),
    TourStep('Rebalance', 'Set category targets',
        'Choose Portfolio to review its category targets. Equities has a 60% target in this demo.',
        ('.st-key-rebalance_tabs [role="tablist"]', '.st-key-tour_category_targets'), 'Targets'),
    TourStep('Rebalance', 'Set targets within a category',
        'Equities targets 70% World ETF and 30% Emerging Markets ETF. Choose Edit targets here or beside the allocation in Overview.',
        ('.st-key-tour_position_targets',), 'Targets', target_scope='equity'),
    TourStep('Rebalance', 'Plan how to invest new money',
        'Choose a planning scope and contribution, then Calculate plan. Options control eligible positions and purchase rules. This demo uses a €500 contribution.',
        ('.st-key-rebalance_tabs [role="tablist"]', '.st-key-planning_scope', '.st-key-planning_amount', '.st-key-planning_calculate'), 'Plan'),
    TourStep('Rebalance', 'Review the proposed changes',
        'The plan shows suggested purchases and their allocation impact. It does not place trades or change your holdings.',
        ('.st-key-tour_plan_results',), 'Plan', calculate_plan=True),
    TourStep('Monitoring', 'Explore a trend before tracking it',
        'Browse starts with MSCI World in EUR and a 200-session SMA. The threshold is blank: prices and SMA are visible, but no rule or alert exists. This tour uses invented history.',
        ('.st-key-tour_monitoring_chart',), 'Browse'),
    TourStep('Monitoring', 'Configure and save a rule',
        'This example chooses below +10%, a 1-point approaching band, a name and pop-up alerts. Track rule saves these settings. Next saves this example only in the temporary tour workspace.',
        ('.st-key-tour_monitoring_condition', '.st-key-tour_monitoring_save'), 'Configure'),
    TourStep('Monitoring', 'Understand a triggered alert',
        'The invented close is at or below this example’s SMA threshold, so the saved rule is Triggered. Check Data and Observed alongside the condition. Checks run only while an app session is open.',
        ('.st-key-tour_monitoring_tracked',), 'Tracked rules'),
    TourStep('Monitoring', 'Acknowledge the alert',
        'These are the same controls shown in an alert pop-up. Try Acknowledge alert: it silences this episode while the rule stays enabled. Recovery to normal rearms it. Finish restores your original workspace and draft.',
        ('.st-key-tour_monitoring_alert',), 'Alert', interactive='.st-key-tour_monitoring_alert'),
)
