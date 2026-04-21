"""POST /api/export — Excel workbook generation endpoint.

Generates a formatted Excel workbook from enriched lead data and returns it
as a streaming download.  Includes verification confidence and sqft source
columns for data-quality transparency.
"""
from __future__ import annotations

from datetime import datetime
from io import BytesIO
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from api._shared.excel import create_workbook

app = FastAPI()

# ---------------------------------------------------------------------------
# Column definitions
# ---------------------------------------------------------------------------
COLUMNS: list[tuple[str, int]] = [
    ("No.", 6),
    ("Postcode", 12),
    ("Company Name", 38),
    ("Business Description", 42),
    ("Address", 48),
    ("Phone", 20),
    ("Website", 34),
    ("Estimated Sq Ft", 18),
    ("Sq Ft Source", 14),
    ("Size Tier", 13),
    ("Confidence", 13),
]

_SQFT_COLUMN_INDEX = 8  # 1-based index of the "Estimated Sq Ft" column

_SQFT_SOURCE_LABELS: dict[str, str] = {
    "osm": "OSM",
    "osm_wide": "OSM (wide)",
    "viewport": "Viewport",
    "category": "Category",
    "none": "",
    "": "",
}


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class LeadRow(BaseModel):
    """A single lead row for export.

    Attributes:
        name: Company name.
        category: Google Places type category.
        address: Full street address.
        phone: Phone number.
        website: Homepage URL.
        postcode: 5-digit Indonesian postcode.
        sqft: Estimated building area in square feet.
        sqft_source: How the sqft was derived (osm, viewport, etc.).
        size_tier: Classified size tier (Small / Medium / Large / Unknown).
        description: AI-generated business description.
        confidence: Verification confidence (high / medium / low / unverified).
    """

    name: str = ""
    category: str = ""
    address: str = ""
    phone: str = ""
    website: str = ""
    postcode: str = ""
    sqft: int | None = None
    sqft_source: str = ""
    size_tier: str = ""
    description: str = ""
    confidence: str = ""


class ExportRequest(BaseModel):
    """Export request body.

    Attributes:
        rows: List of lead rows to include in the workbook.
    """

    rows: list[LeadRow]


# ---------------------------------------------------------------------------
# Value extractor
# ---------------------------------------------------------------------------
def _extract_row_values(row_index: int, row_data: dict[str, Any]) -> list[Any]:
    """Map a lead dict to an ordered list of cell values.

    Args:
        row_index: 1-based row number (used as the "No." column).
        row_data: Dict with lead fields.

    Returns:
        List of values matching the ``COLUMNS`` order.
    """
    source = row_data.get("sqft_source", "")
    return [
        row_index,
        row_data.get("postcode", ""),
        row_data.get("name", ""),
        row_data.get("description", ""),
        row_data.get("address", ""),
        row_data.get("phone", ""),
        row_data.get("website", ""),
        row_data.get("sqft"),
        _SQFT_SOURCE_LABELS.get(source, source),
        row_data.get("size_tier", ""),
        (row_data.get("confidence", "") or "").capitalize(),
    ]


# ---------------------------------------------------------------------------
# Endpoint
# ---------------------------------------------------------------------------
@app.post("/api/export")
async def export(body: ExportRequest) -> StreamingResponse:
    """Generate and stream an Excel workbook from lead data.

    Args:
        body: Request containing lead rows.

    Returns:
        A streaming response with the ``.xlsx`` file as an attachment.

    Raises:
        HTTPException: If the request contains no rows.
    """
    if not body.rows:
        raise HTTPException(status_code=400, detail="No data to export.")

    rows_dicts = [r.model_dump() for r in body.rows]
    wb = create_workbook(
        columns=COLUMNS,
        rows=rows_dicts,
        value_extractor=_extract_row_values,
        sheet_title="PE Deal Leads",
        number_format_columns={_SQFT_COLUMN_INDEX},
    )

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"PE_Leads_{datetime.now().strftime('%Y-%m-%d')}.xlsx"
    return StreamingResponse(
        buffer,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
