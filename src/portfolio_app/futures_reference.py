"""Narrow public ECB/IBKR adapters; dated observations stay in private storage."""
from datetime import datetime, timezone
from html.parser import HTMLParser
import json
import math
from urllib.request import Request, urlopen

from portfolio_app.holdings import DataError
from portfolio_app.storage import revision, save_document

ECB_URL = 'https://www.ecb.europa.eu/stats/financial_markets_and_interest_rates/euro_short-term_rate/html/index.en.html'
IBKR_URL = 'https://www.interactivebrokers.ie/en/trading/margin-futures-fops.php?ex=eu&hm=eu&pm=0&rgt=0&rsk=1&rst=040404040404040104'
REFERENCE_FILE = 'futures-reference.json'


class TableRows(HTMLParser):
    def __init__(self):
        super().__init__()
        self.rows, self.row, self.cell = [], [], None

    def handle_starttag(self, tag, attrs):
        if tag == 'tr':
            self.row = []
        if tag in ('td', 'th'):
            self.cell = []

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag in ('td', 'th') and self.cell is not None:
            self.row.append(' '.join(' '.join(self.cell).split()))
            self.cell = None
        if tag == 'tr' and self.row:
            self.rows.append(self.row)
            self.row = []


def parse_references(ecb_html: str, ibkr_html: str) -> dict:
    ecb, ibkr = TableRows(), TableRows()
    ecb.feed(ecb_html)
    ibkr.feed(ibkr_html)
    try:
        rate = float(next(row[1] for row in ecb.rows if row[0] == 'Rate')) / 100
        day = datetime.strptime(next(row[1] for row in ecb.rows if row[0] == 'Reference date'), '%d-%m-%Y').date().isoformat()
        # Match the instrument, exchange, currency AND column layout, never a
        # similarly named USD World future or a borrowing-interest-rate table.
        headers = next(row for row in ibkr.rows if 'Short Overnight Initial' in row)
        if headers[6:9] != ['Overnight Initial', 'Overnight Maintenance', 'Currency'] or headers[10:12] != ['Short Overnight Initial', 'Short Overnight Maintenance']:
            raise ValueError('IBKR margin table layout changed')
        row = next(row for row in ibkr.rows if len(row) >= 12 and row[0] == 'EUREX' and row[3] == 'FMWN' and row[8] == 'EUR')
        margins = [float(row[index].replace(',', '')) for index in (6, 7, 10, 11)]
        if not all(math.isfinite(value) and value > 0 for value in margins) or margins[1] > margins[0] or margins[3] > margins[2]:
            raise ValueError('Invalid published margin values')
        if not math.isfinite(rate) or not -.1 < rate < 1:
            raise ValueError('Invalid ECB rate')
        return dict(estr_rate=rate, estr_reference_date=day, long_initial=margins[0], long_maintenance=margins[1],
                    short_initial=margins[2], short_maintenance=margins[3], ecb_source=ECB_URL, ibkr_source=IBKR_URL,
                    fetched_at=datetime.now(timezone.utc).isoformat())
    except (StopIteration, IndexError, ValueError) as exc:
        raise DataError(f'Could not verify ECB/IBKR reference data: {exc}') from exc


def refresh_references(directory, fetch=None):
    def download(url):
        with urlopen(Request(url, headers={'User-Agent': 'Mozilla/5.0'}), timeout=15) as response:
            return response.read().decode('utf-8')
    fetch = fetch or download
    path = directory / REFERENCE_FILE
    expected = revision(path)
    result = parse_references(fetch(ECB_URL), fetch(IBKR_URL))
    save_document(path, json.dumps(result, indent=2) + '\n', expected)
    return result


def load_references(directory):
    path = directory / REFERENCE_FILE
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text())
        for field in ('estr_rate', 'long_initial', 'long_maintenance', 'short_initial', 'short_maintenance'):
            if not math.isfinite(raw[field]):
                raise ValueError('Invalid reference number')
        datetime.fromisoformat(raw['fetched_at'])
        datetime.fromisoformat(raw['estr_reference_date'])
        return raw
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise DataError(f'Invalid saved futures reference: {exc}') from exc


def estr_reference_for_start(directory, reference, start):
    """Use a dated snapshot, or saved ECB history if that snapshot is too new.

    Both forward cash and carry must resolve to the same starting observation.
    This reads existing history only; it never downloads or rewrites data.
    """
    if reference and reference['estr_reference_date'] <= start.isoformat():
        return reference
    from portfolio_app.futures_history_data import load_history, unpack_history
    history = load_history(directory)
    if history is None:
        return None
    _, rates = unpack_history(history)
    known = rates.loc[:start.isoformat()]
    if known.empty or (start-known.index[-1].date()).days > 7:
        return None
    return dict(estr_rate=float(known.iloc[-1]), estr_reference_date=known.index[-1].date().isoformat())
