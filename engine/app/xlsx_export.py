"""Formatted Excel downloads (orders and project results): bold frozen header, filters, sensible widths,
wrapped long text, clickable links, and a status dropdown when there's a Status column."""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

HEADER_FILL = PatternFill("solid", fgColor="1E3A8A")
HEADER_FONT = Font(bold=True, color="FFFFFF")
LINK_FONT = Font(color="1D4ED8", underline="single")
WIDE = {"why it's a lead": 60, "suggested email": 60, "notes": 50, "signal": 45, "note": 50, "also hiring": 30,
        "posted on": 26, "decision maker (title)": 28}
STATUSES = ["new", "contacted", "meeting", "placement", "not relevant"]


def _width(header: str, values: list[Any]) -> float:
    h = header.lower()
    if h in WIDE:
        return WIDE[h]
    longest = max([len(header)] + [len(str(v)) for v in values[:200] if v is not None])
    return max(10, min(40, longest + 2))


def workbook(columns: list[str], rows: list[list[Any]], *, title: str = "Leads", summary: list[str] | None = None,
             extra_sheets: dict[str, str] | None = None, header_lines: list[str] | None = None) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = title[:31]
    top = 0
    for line in header_lines or []:        # what the client must read first, above the table
        ws.append([line])
        top += 1
        c = ws.cell(row=top, column=1)
        c.font = Font(bold=True, color="92400E")
        c.fill = PatternFill("solid", fgColor="FEF3C7")
        c.alignment = Alignment(wrap_text=True, vertical="top")
        ws.merge_cells(start_row=top, start_column=1, end_row=top, end_column=max(1, min(len(columns), 10)))
        ws.row_dimensions[top].height = max(18, 15 * (len(line) // 140 + 1))
    if top:
        ws.append([])
        top += 1
    head = top + 1
    ws.append(columns)
    for c in ws[head]:
        c.fill, c.font = HEADER_FILL, HEADER_FONT
        c.alignment = Alignment(vertical="center", wrap_text=True)
    ws.row_dimensions[head].height = 30
    for r in rows:
        ws.append([("" if v is None else v) for v in r])
    last = ws.max_row
    for j, name in enumerate(columns, 1):
        col = get_column_letter(j)
        values = [row[j - 1] if j - 1 < len(row) else "" for row in rows]
        ws.column_dimensions[col].width = _width(name, values)
        wrap = ws.column_dimensions[col].width >= 40
        for i in range(head + 1, last + 1):
            cell = ws.cell(row=i, column=j)
            v = cell.value
            if isinstance(v, str) and v.startswith(("http://", "https://")):
                cell.hyperlink = v
                cell.value = "Open" if len(v) > 45 else v
                cell.font = LINK_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=wrap)
    ws.freeze_panes = f"C{head + 1}" if len(columns) > 3 else f"A{head + 1}"
    if rows:
        ws.auto_filter.ref = f"A{head}:{get_column_letter(len(columns))}{last}"
    if "Status" in columns and rows:
        col = get_column_letter(columns.index("Status") + 1)
        dv = DataValidation(type="list", formula1='"' + ",".join(STATUSES) + '"', allow_blank=True)
        ws.add_data_validation(dv)
        dv.add(f"{col}{head + 1}:{col}{max(last, head + 1) + 500}")
    if summary:
        ws.append([])
        for line in summary:
            ws.append([line])
            ws.cell(row=ws.max_row, column=1).font = Font(italic=True, color="475569")
    for name, text in (extra_sheets or {}).items():
        if not text:
            continue
        s = wb.create_sheet(name[:31])
        s.column_dimensions["A"].width = 110
        for line in str(text).split("\n"):
            s.append([line])
            s.cell(row=s.max_row, column=1).alignment = Alignment(wrap_text=True, vertical="top")
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
