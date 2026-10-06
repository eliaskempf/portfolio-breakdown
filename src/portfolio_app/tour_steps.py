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

    @property
    def highlight_groups(self) -> tuple[tuple[str, ...], ...]:
        return tuple((anchor,) for anchor in self.detached_anchors) + (self.anchors,)


STEPS = (
    TourStep('Overview', 'Four ways to understand your portfolio',
        'Overview shows what you hold and its allocation. Exposure reveals the companies and regions underneath. Positions manages your holdings. Rebalance plans changes toward your targets.',
        ('.st-key-main_tabs > div > [role="tablist"]',)),
    TourStep('Overview', 'Choose the portfolio you want to examine',
        'Category focuses the overview on your whole portfolio or a subset. Current value and Portfolio share follow that selection.',
        ('.st-key-tour_overview_scope',)),
    TourStep('Overview', 'See where your money is allocated',
        'The sunburst and table show the same allocation. Try clicking a chart category to explore it, then click the center to go back. The table follows your selection and compares current weights with targets.',
        ('.st-key-strategic_view', '.st-key-overview_allocation'), 'Allocation', interactive='.st-key-overview_allocation .js-plotly-plot'),
    TourStep('Overview', 'Compare gains and losses',
        'Performance compares current values with recorded buy-in costs. Switch between percentage return and euro gain. This excludes dividends and realized gains.',
        ('.st-key-strategic_view', '.st-key-tour_performance'), 'Performance'),
    TourStep('Overview', 'Understand risk and diversification',
        'Calculate risk estimates volatility, sensitivity to a benchmark, and correlation. We have calculated this example using synthetic history. It describes today’s allocation, not your personal historical return.',
        ('.st-key-strategic_view', '.st-key-tour_risk_controls', '.st-key-tour_risk_metrics'), 'Analytics'),
    TourStep('Exposure', 'Look inside your ETFs',
        'Break down ETFs replaces supported funds with their underlying holdings. Here it is off, so you see the securities held directly. Next, we will turn it on.',
        ('.st-key-tour_etf_breakdown',), 'Assets', lookthrough=False),
    TourStep('Exposure', 'Discover what you actually own',
        'The table now includes ETF constituents, combined with any direct holdings in the same asset. Uncovered ETF weight stays visible as Other; unsupported funds remain whole.',
        ('.st-key-exposure_view', '.st-key-tour_exposure_assets'), 'Assets', detached_anchors=('.st-key-tour_etf_breakdown',)),
    TourStep('Exposure', 'Explore themes and sectors',
        'Group the underlying exposure by a classification such as sector. Explore the hierarchy to see how broad categories break down into more specific themes.',
        ('.st-key-exposure_view', '.st-key-exposure_group', '.st-key-exposure_theme_results'), 'Themes & sectors'),
    TourStep('Exposure', 'See your geographic exposure',
        'Compare regions or countries, then choose an area to explore its detail. Unknown geography remains explicit rather than being assigned to a country.',
        ('.st-key-exposure_view', '.st-key-tour_geography'), 'Geography'),
    TourStep('Positions', 'Add and manage your holdings',
        'Add position creates a holding. Select a row to inspect a position and its price history; use its pencil to edit quantities, buy-ins, and other details.',
        ('.st-key-tour_add_position', '.st-key-tour_position_list')),
    TourStep('Positions', 'Keep your portfolio up to date',
        'Use Bulk add purchases to record several purchases, Update balances to adjust holdings, or Import portfolio to bring in an existing portfolio.',
        ('.st-key-positions_workflow',)),
    TourStep('Rebalance', 'Set category targets',
        'Category targets divide money between categories, relative to their parent. In this demo, Equities targets 60% of the whole portfolio. Subcategories use their parent category as the total.',
        ('.st-key-rebalance_tabs [role="tablist"]', '.st-key-tour_category_targets'), 'Targets'),
    TourStep('Rebalance', 'Set targets within a category',
        'Position targets divide a category between its holdings. Within Equities, this demo targets 70% World ETF and 30% Emerging Markets ETF. Both percentages are shares of Equities.',
        ('.st-key-tour_position_targets',), 'Targets'),
    TourStep('Rebalance', 'Plan how to invest new money',
        'Choose a planning scope and contribution, then Calculate plan. Options control eligible positions and purchase rules. This demo uses a €500 contribution.',
        ('.st-key-rebalance_tabs [role="tablist"]', '.st-key-planning_scope', '.st-key-planning_amount', '.st-key-planning_calculate'), 'Plan'),
    TourStep('Rebalance', 'Review the proposed changes',
        'The plan shows suggested purchases and their allocation impact. It does not place trades or change your holdings. Finish returns you to the portfolio and screen where you started.',
        ('.st-key-tour_plan_results',), 'Plan', calculate_plan=True),
)
