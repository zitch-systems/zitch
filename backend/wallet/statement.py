"""Account statements: the customer's ledger as a file they can send to someone.

Two formats, one row set. PDF is the branded document (rendered by the shared
`whatsapp.receipt` drawing code, so a statement and a receipt look like they came
from the same bank); XLSX is the working copy someone reconciles in a spreadsheet.

The XLSX is written by hand rather than with openpyxl/xlsxwriter. An .xlsx is a
zip of a handful of XML parts, `zipfile` is in the standard library, and the
alternative is a compiled dependency on the deploy image for the sake of one
four-column sheet.
"""
import io
import zipfile
from xml.sax.saxutils import escape

def _sheet_xml(rows: list[dict]) -> str:
    """The worksheet part. Everything is written as an inline string (`t="inlineStr"`)
    so there is no shared-string table to keep in sync — a four-column export does
    not benefit from the de-duplication that table exists for."""
    head = ["Date", "Description", "Type", "Amount (NGN)", "Status", "Reference"]

    def cell(ref: str, value: str, *, number: bool = False) -> str:
        if number:
            return f'<c r="{ref}"><v>{value}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(value))}</t></is></c>'

    cols = "ABCDEF"
    body = ["".join(cell(f"{cols[i]}1", h) for i, h in enumerate(head))]
    for n, r in enumerate(rows, start=2):
        body.append(
            cell(f"A{n}", r.get("date", ""))
            + cell(f"B{n}", r.get("label", ""))
            + cell(f"C{n}", "Credit" if r.get("direction") == "in" else "Debit")
            # Signed and numeric, not a formatted string: the point of the
            # spreadsheet format is that the column can be summed, and "₦1,000.00"
            # is text to Excel.
            + cell(f"D{n}", r.get("signed_amount", "0"), number=True)
            + cell(f"E{n}", r.get("status", ""))
            + cell(f"F{n}", r.get("reference", ""))
        )
    sheet_rows = "".join(
        f'<row r="{i + 1}">{cells}</row>' for i, cells in enumerate(body)
    )
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<cols>'
        '<col min="1" max="1" width="20" customWidth="1"/>'
        '<col min="2" max="2" width="38" customWidth="1"/>'
        '<col min="3" max="3" width="10" customWidth="1"/>'
        '<col min="4" max="4" width="16" customWidth="1"/>'
        '<col min="5" max="5" width="14" customWidth="1"/>'
        '<col min="6" max="6" width="26" customWidth="1"/>'
        '</cols>'
        f'<sheetData>{sheet_rows}</sheetData>'
        '</worksheet>'
    )


def build_statement_xlsx(rows: list[dict], sheet_name: str = "Statement") -> bytes:
    """Return .xlsx bytes for `rows` (same shape the PDF renderer takes, plus
    `direction` and `signed_amount`)."""
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '</Types>'
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        '</Relationships>'
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        f'<sheets><sheet name="{escape(sheet_name[:31])}" sheetId="1" r:id="rId1"/></sheets>'
        '</workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '</Relationships>'
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", _sheet_xml(rows))
    return buf.getvalue()
