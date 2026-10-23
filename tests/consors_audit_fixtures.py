"""Invented statements, authored independently of the importer and old generator.

Only layout conventions come from public Consors format descriptions. Values,
identities and dates are invented. No parser helpers calculate fixture totals.
"""
import pymupdf

BUY = (
    'Wertpapierabrechnung Kauf AM 13.03.2025 UM 09:42:10',
    'SYNTHETIC ORCHARD SYN777 XX0000000777',
    'ST 4,00000',
    'Preis pro Anteil 31,250000 EUR NETTO',
    'Kurswert EUR 125,00',
    'Provision 1,00 EUR',
    'Zulasten IBAN SYNTHETIC 126,00 EUR',
    'Valuta 17.03.2025',
)
FREE = (*BUY[:5], 'Zulasten IBAN SYNTHETIC 125,00 EUR', BUY[-1])
COMPANY_FOOTER = (
    'Standort Testort: Musterweg 8, 00000 Testort, '
    'HRB Testort 12345, USt-IdNr. DE000000000')
DOCUMENT_REFERENCE = '00000000-0000-0000-1ABC-000000000001'
SALE = (
    'VERKAUF AM 18.03.2025 UM 14:13:12',
    'SYNTHETIC ORCHARD SYN777 XX0000000777',
    'ST 2,00000',
    'KURS 45,000000 EUR P.ST. FRANCO COURTAGE',
    'KURSWERT EUR 90,00',
    'Handelsplatzkosten EUR 0,50',
    'Zugunsten IBAN SYNTHETIC EUR 88,50',
    'Wert 20.03.2025',
)
LOSS_SALE = tuple(r.replace('45,000000', '20,000000').replace('90,00', '40,00')
                  .replace('88,50', '39,50') for r in SALE)
SUPPLEMENTARY_DETAILS = (
    'Informationen bei Käufen bzw. Verlust Wertpapiere ungleich Aktien',
    'Veräusserungsverlust nach Differenzmethode - 22,50 EUR',
    'Details zur Investmentbesteuerung',
    'Neue synthetische Rechengröße - 11,25 EUR',
    'in Verrechnungstopf „Allgemein“ eingestellt 22,50 EUR',
)
TAX_PAGE = (
    'Steuerberechnung',
    'KapSt-pflichtiger Kapitalertrag - 17,00 EUR',
    'Kapitalertragsteuer-Bemessungsgrundlage 4,00 EUR',
    'KAPST anteilig 50,00% 25,00% EUR 0,50',
    'KAPST anteilig 50,00% 25,00% EUR 0,50',
    'CBL 000987',
    'Seite 2 VON 2',
)
# Independently invented calculation amounts; none are transaction charges.
TAX_INFORMATION = (
    'Veräusserungsgewinn nach Differenzmethode 20,00 EUR',
    'KapSt-pflichtige Kapitalerträge 20,00 EUR',
    'Berücksichtigung Teilfreistellung 17,50 % - 3,50 EUR',
    'Ergebnis nach Teilfreistellung / Vorabpauschale 16,50 EUR',
    'Steuerpflichtiger Gesamtbetrag 16,50 EUR',
    'mit Verrechnungstopf „Allgemein“ verrechnet - 12,50 EUR',
    'mit Sparerpauschbetrag verrechnet - 4,00 EUR',
    'Bemessungsgrundlage für Kapitalertragsteuer 0,00 EUR',
    'Sparerpauschbetrag nach dem Geschäft 246,00 EUR',
    'Verrechnungstopf Aktien nach dem Geschäft 0,00 EUR',
    'Verrechnungstopf Allg. nach dem Geschäft 0,00 EUR',
    'Verrechnungstopf QUST nach dem Geschäft 0,00 EUR',
)
LEGACY = (
    'KAUF AM 12.02.2025 IN SPARPLAN NR. SYNTHETIC',
    'ST 0,62500 WKN: SYN777',
    'SYNTHETIC ORCHARD',
    'Preis pro Anteil 32,000000 EUR',
    'KURSWERT EUR 20,00',
    'ZULASTEN EUR 20,00',
    'VALUTA 14.02.2025',
)


def text_pdf(*pages):
    """Minimal PDF serializer: full visual lines, no PyMuPDF writing machinery."""
    objects = [b'', b'', b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>']
    kids = []
    for lines in pages:
        page_id = len(objects)+1
        stream_id = page_id+1
        kids.append(f'{page_id} 0 R')
        objects.append(f'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R >> >> /Contents {stream_id} 0 R >>'.encode())
        commands = [b'BT /F1 10 Tf 18 TL 40 750 Td']
        for index, line in enumerate(lines):
            escaped = line.encode('cp1252').replace(b'\\', b'\\\\').replace(b'(', b'\\(').replace(b')', b'\\)')
            commands.append((b'T* ' if index else b'')+b'('+escaped+b') Tj')
        commands.append(b'ET')
        stream = b'\n'.join(commands)
        objects.append(f'<< /Length {len(stream)} >>\nstream\n'.encode()+stream+b'\nendstream')
    objects[0] = b'<< /Type /Catalog /Pages 2 0 R >>'
    objects[1] = f'<< /Type /Pages /Count {len(pages)} /Kids [{" ".join(kids)}] >>'.encode()
    output = bytearray(b'%PDF-1.4\n')
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f'{index} 0 obj\n'.encode()+obj+b'\nendobj\n')
    start = len(output)
    output.extend(f'xref\n0 {len(objects)+1}\n0000000000 65535 f \n'.encode())
    for offset in offsets[1:]:
        output.extend(f'{offset:010} 00000 n \n'.encode())
    output.extend(f'trailer << /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF'.encode())
    return bytes(output)


def cell_pdf(*, reverse=False, jitter=0):
    """Independently authored column layout, with displaced cell baselines."""
    cells = [
        (32, 44, 'Kauf'), (158, 44, 'AM 13.03.2025 UM 09:42:10'),
        (32, 92, 'SYNTHETIC ORCHARD'), (280, 92, 'SYN777'), (390, 92, 'XX0000000777'),
        (32, 127, 'ST'), (158, 127, '4,00000'),
        (32, 170, 'Preis pro Anteil'), (280, 170, '31,250000'), (390, 170, 'EUR NETTO'),
        (32, 216, 'Kurswert'), (280, 216, 'EUR'), (390, 216, '125,00'),
        (32, 250, 'Provision'), (280, 250, '1,00'), (390, 250, 'EUR'),
        (32, 294, 'Zulasten IBAN SYNTHETIC'), (280, 294, '126,00'), (390, 294, 'EUR'),
        (32, 335, 'Valuta 17.03.2025'),
    ]
    with pymupdf.open() as doc:
        page = doc.new_page()
        for x, y, word in reversed(cells) if reverse else cells:
            page.insert_text((x, y+(jitter if x >= 280 else 0)), word, fontsize=9)
        return doc.tobytes()


def with_margin_reference(source, reference=DOCUMENT_REFERENCE, *, rotation=90):
    """Add an invented reference as real rotated PDF text, not a picture."""
    with pymupdf.open(stream=source, filetype='pdf') as doc:
        point = (580, 520) if rotation == 90 else (580, 300) if rotation == 270 else (40, 300)
        doc[0].insert_text(point, reference, fontsize=8, rotate=rotation)
        return doc.tobytes()
