"""Verify real spotlight cutouts, demo navigation, and restoration in a browser."""
import pytest
from portfolio_app.tour_steps import STEPS
from test_intro_browser import intro_server, intro_page  # noqa: F401

playwright = pytest.importorskip('playwright.sync_api')


def step(page, index):
    card = page.locator('.st-key-app_tour')
    playwright.expect(card).to_contain_text(f'{index + 1} of {len(STEPS)}')
    overlay = page.locator('.portfolio-tour-overlay')
    playwright.expect(overlay).to_have_attribute('data-step', str(index))
    playwright.expect(overlay).to_have_attribute('data-ready', 'true')
    playwright.expect(page.get_by_role('tab', name=STEPS[index].tab, exact=True)).to_have_attribute('aria-selected', 'true')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    playwright.expect(page.get_by_test_id('stApp')).to_have_attribute('data-test-script-state', 'notRunning')
    holes = page.locator('#portfolio-tour-mask rect[data-tour-target]')
    expected_groups = STEPS[index].highlight_groups
    assert 0 < holes.count() <= len(expected_groups)
    playwright.expect(page.locator('[data-tour-border]')).to_have_count(holes.count())
    for hole in holes.all():
        assert hole.get_attribute('fill') == 'black'
        group = next(group for group in expected_groups if ','.join(group) == hole.get_attribute('data-tour-target'))
        rects = [page.locator(selector).bounding_box() for selector in group]
        left = min(r['x'] for r in rects)
        top = min(r['y'] for r in rects)
        right = max(r['x'] + r['width'] for r in rects)
        assert abs(float(hole.get_attribute('x')) - max(4, left - 6)) < 2
        assert abs(float(hole.get_attribute('y')) - max(4, top - 6)) < 2
        assert abs(float(hole.get_attribute('width')) - (min(page.viewport_size['width'] - 4, right + 6) - max(4, left - 6))) < 2
    for label in ('Skip tour', 'Finish' if index == len(STEPS)-1 else 'Next'):
        button = card.get_by_role('button', name=label, exact=True)
        box = button.bounding_box()
        assert 0 <= box['y'] and box['y'] + box['height'] <= page.viewport_size['height']
        assert button.evaluate('(el) => { const r=el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.x+r.width/2,r.y+r.height/2)); }')
    return card


def click_chart_slice(page, label):
    page.wait_for_function("!!document.querySelector('.js-plotly-plot')?._ev?._events?.plotly_sunburstclick")
    target = page.locator(f'.st-key-overview_allocation text[data-unformatted="{label}"]')
    playwright.expect(target).to_be_visible()
    bounds = target.bounding_box()
    x, y = bounds['x'] + bounds['width'] / 2, bounds['y'] + bounds['height'] / 2
    assert page.evaluate('([x,y]) => !!document.elementFromPoint(x,y)?.closest(".js-plotly-plot")', [x,y])
    page.mouse.click(x, y)


def enter(page, url, manual=False):
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Start my portfolio' if manual else 'Explore demo', exact=True).click()
    if manual:
        page.get_by_role('button', name='Skip setup', exact=True).click()
    welcome = page.get_by_role('dialog', name='Welcome to Breakdown!', exact=True)
    playwright.expect(welcome).to_be_visible()
    welcome.get_by_role('button', name='Take the tour', exact=True).click()
    return step(page, 0)


def test_demo_tour_every_view_and_menu_replay(intro_page):
    page, url, directory = intro_page
    errors = []
    page.on('pageerror', lambda error: errors.append(error))
    card = enter(page, url)
    card.get_by_role('button', name='Next', exact=True).focus()
    page.keyboard.press('Enter')
    card = step(page, 1)
    card.get_by_role('button', name='Back', exact=True).click()
    step(page, 0)
    for index in range(1, len(STEPS)):
        card.get_by_role('button', name='Next', exact=True).click()
        card = step(page, index)
        if index == 2:
            playwright.expect(page.locator('.js-plotly-plot').first).to_be_visible()
            page.screenshot(path=str(directory / 'synthetic-allocation-tour.png'))
            click_chart_slice(page, 'Equities')
            playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Equities')
            step(page, 2)
            # The center of the drilled chart returns to the full portfolio.
            click_chart_slice(page, 'Equities')
            playwright.expect(page.get_by_role('combobox', name='Category', exact=True)).to_have_value('Portfolio')
            step(page, 2)
        if index == 4:
            playwright.expect(page.locator('.st-key-tour_risk')).to_contain_text('Annualized volatility')
        if index in (11, 12):
            expected = '.st-key-tour_category_targets' if index == 11 else '.st-key-tour_position_targets'
            other = '.st-key-tour_position_targets' if index == 11 else '.st-key-tour_category_targets'
            targets = page.locator('#portfolio-tour-mask [data-tour-target]').get_attribute('data-tour-target')
            assert expected in targets and other not in targets
            playwright.expect(card).to_contain_text('60%' if index == 11 else '70%')
    card.get_by_role('button', name='Finish', exact=True).click()
    playwright.expect(card).to_have_count(0)
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_count(0)
    page.get_by_role('tab', name='Exposure', exact=True).click()
    page.get_by_role('textbox', name='Search exposure', exact=True).fill('Nvidia')
    page.get_by_role('textbox', name='Search exposure', exact=True).press('Enter')
    page.get_by_role('button', name='?', exact=True).click()
    page.get_by_role('button', name='Take the tour', exact=True).click()
    step(page, 0)
    playwright.expect(page.get_by_role('button', name='Take the tour', exact=True)).to_be_hidden()
    page.keyboard.press('Escape')
    playwright.expect(card).to_have_count(0)
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_count(0)
    playwright.expect(page.get_by_role('tab', name='Exposure', exact=True)).to_have_attribute('aria-selected', 'true')
    playwright.expect(page.get_by_role('textbox', name='Search exposure', exact=True)).to_have_value('Nvidia')
    assert not errors


@pytest.mark.parametrize('width,height', [(390, 844), (390, 568)])
def test_empty_portfolio_uses_demo_and_returns_on_skip(intro_page, width, height):
    page, url, directory = intro_page
    page.set_viewport_size({'width':width, 'height':height})
    card = enter(page, url, manual=True)
    for index in range(1, len(STEPS)):
        card.get_by_role('button', name='Next', exact=True).click()
        card = step(page, index)
    page.screenshot(path=str(directory / 'synthetic-mobile-tour.png'))
    card.get_by_role('button', name='Skip tour', exact=True).click()
    playwright.expect(card).to_have_count(0)
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_count(0)
    playwright.expect(page.get_by_role('combobox', name='Portfolio workspace', exact=True)).to_have_value('My portfolio')
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
    playwright.expect(page.get_by_test_id('stException')).to_have_count(0)
    assert not (directory / 'empty' / 'holdings.csv').exists()


@pytest.mark.parametrize('theme', ['Light', 'Dark'])
def test_tour_theme_resize_scroll_and_keyboard_exit(intro_page, theme):
    page, url, _ = intro_page
    page.emulate_media(reduced_motion='reduce')
    page.goto(url)
    page.get_by_role('button', name='Explore demo', exact=True).click()
    page.get_by_role('button', name='Not now', exact=True).click()
    page.get_by_role('button', name='Main menu', exact=True).click()
    page.get_by_test_id(f'stMainMenuItem-theme-{theme}').click()
    page.keyboard.press('Escape')
    page.get_by_role('button', name='?', exact=True).click()
    page.get_by_role('button', name='Take the tour', exact=True).click()
    card = step(page, 0)
    playwright.expect(card).to_have_attribute('data-tour-theme', theme.lower())
    playwright.expect(card).to_have_css('background-color', 'rgb(255, 255, 255)' if theme == 'Light' else 'rgb(26, 32, 43)')
    colors = card.evaluate("""el => ({background:getComputedStyle(el).backgroundColor,
        text:[...el.querySelectorAll('h3, [data-testid="stMarkdownContainer"] p, [data-testid="stCaptionContainer"] p')]
          .filter(p=>!p.closest('button')).map(p=>getComputedStyle(p).color)})""")
    import re
    def luminance(rgb):
        channels = [int(v)/255 for v in re.findall(r'\d+', rgb)[:3]]
        linear = [v/12.92 if v <= .04045 else ((v+.055)/1.055)**2.4 for v in channels]
        return sum(v*w for v,w in zip(linear, (.2126,.7152,.0722)))
    background = luminance(colors['background'])
    assert colors['text']
    for color in colors['text']:
        foreground = luminance(color)
        assert (max(background,foreground)+.05)/(min(background,foreground)+.05) >= 4.5
    for index in (1, 2):
        card.get_by_role('button', name='Next', exact=True).click()
        step(page, index)
    page.set_viewport_size({'width':650, 'height':500})
    step(page, 2)
    page.mouse.move(100, 120)
    before = page.get_by_test_id('stMain').evaluate('el => el.scrollTop')
    page.mouse.wheel(0, 160)
    page.wait_for_function("""(before) => document.querySelector('[data-testid="stMain"]').scrollTop > before""", arg=before)
    step(page, 2)
    page.keyboard.press('Escape')
    playwright.expect(page.locator('.portfolio-tour-overlay')).to_have_count(0)
    playwright.expect(page.get_by_role('tab', name='Overview', exact=True)).to_have_attribute('aria-selected', 'true')
