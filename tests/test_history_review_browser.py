"""Review progression with synthetic holdings and delayed browser updates."""
from time import monotonic

from streamlit.proto.ForwardMsg_pb2 import ForwardMsg
from portfolio_app.positions import read_snapshot
from portfolio_app.account_journal import load_journal
from portfolio_app.security_history import effective_holdings

from test_import_browser import import_page
from test_csv_transaction_import_browser import upload, settle, playwright


def test_delayed_accept_click_cannot_accept_an_unseen_holding(import_page):
    page, path = import_page
    held, pending, finished = [False], [], []
    def route(ws):
        server = ws.connect_to_server()
        def receive(message):
            if not held[0]:
                ws.send(message)
                return
            pending.append((ws, message))
            if isinstance(message, bytes):
                frame = ForwardMsg()
                frame.ParseFromString(message)
                if frame.WhichOneof('type') == 'script_finished':
                    finished.append(True)
        server.on_message(receive)
    page.route_web_socket('**/_stcore/stream', route)
    page.reload()
    page.get_by_role('button', name='Import transactions', exact=True).wait_for()
    quantities = dict(zip(('AUR', 'BRN', 'CEL', 'DUS', 'EKO', 'FLX'), range(1, 7)))
    content = 'reference,date,type,asset,quantity,amount,currency\n'+''.join(
        f'invented-{asset},2024-01-02,buy,{asset},{quantity},10,EUR\n'
        for asset, quantity in quantities.items())
    upload(page, content.encode())
    page.get_by_role('button', name='Review against portfolio', exact=True).click(); settle(page)
    holding = page.get_by_role('combobox', name='Holding to review', exact=True)
    first = holding.input_value()
    accept = page.get_by_role('button', name='Accept this holding', exact=True)
    held[0] = True
    accept.click()
    deadline = monotonic()+10
    while not finished and monotonic() < deadline:
        page.wait_for_timeout(25)
    assert finished, 'Server did not finish the first acceptance'
    assert holding.input_value() == first
    assert accept.is_enabled(), 'Old accept control should remain clickable during this delayed-render reproduction'
    accept.click()
    deadline = monotonic()+10
    while len(finished) < 2 and monotonic() < deadline:
        page.wait_for_timeout(25)
    assert len(finished) >= 2
    held[0] = False
    for ws, message in pending:
        ws.send(message)
    settle(page)
    playwright.expect(page.get_by_text('Reviewed 1 / 6', exact=True)).to_be_visible()
    assert holding.input_value() != first, 'A stale dropdown value must not undo the automatic advance'
    assert not path.exists(), 'Acceptance must not publish holdings'
    reviewed = {first}
    apply = page.get_by_role('button', name='Apply reviewed holdings', exact=True)
    for count in range(2, 7):
        shown = holding.input_value()
        assert shown not in reviewed, 'A single click should advance to an unreviewed holding'
        shares = page.get_by_role('row').filter(has=page.get_by_text('Shares', exact=True)).get_by_role('cell').nth(2)
        playwright.expect(shares).to_have_text(str(quantities[shown]))
        playwright.expect(apply).to_be_disabled()
        accept.click(); settle(page)
        reviewed.add(shown)
        playwright.expect(page.get_by_text(f'Reviewed {count} / 6', exact=True)).to_be_visible()
    assert reviewed == set(quantities)
    playwright.expect(accept).to_be_disabled()
    playwright.expect(apply).to_be_enabled()
    holding.click()
    page.get_by_role('option', name=first+' · Reviewed', exact=True).click(); settle(page)
    playwright.expect(accept).to_be_disabled()
    apply.click(); settle(page)
    saved = effective_holdings(read_snapshot(path).holdings, load_journal(path.parent)[0].securities)
    assert dict(zip(saved.name, saved.shares)) == quantities
