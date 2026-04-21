"""/api/saves — CRUD for saved search results using Redis.

Endpoints:
    - ``GET  /api/saves``         — list all saved searches (metadata only)
    - ``GET  /api/saves?id=X``    — load a specific saved search (full data)
    - ``POST /api/saves``         — save a search result
    - ``DELETE /api/saves?id=X``  — delete a saved search
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api._shared.redis_client import redis_connection

logger = logging.getLogger(__name__)

app = FastAPI()

_MAX_SAVED_SEARCHES = 50


# ---------------------------------------------------------------------------
# Request model
# ---------------------------------------------------------------------------
class SaveRequest(BaseModel):
    """Request body for saving a search.

    Attributes:
        name: Human-readable label for the saved search.
        postcodes: Comma-separated postcodes that were searched.
        places: Full list of place result dicts.
    """

    name: str = ""
    postcodes: str = ""
    places: list[dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.post("/api/saves")
async def save_search(body: SaveRequest) -> JSONResponse:
    """Persist a search result to Redis.

    Creates a unique timestamp-based ID, stores the full result under
    ``save:<id>``, and prepends metadata to the ``saves:index`` list.

    Args:
        body: The search data to persist.

    Returns:
        JSON with ``id`` and ``message`` on success, or an error payload.
    """
    with redis_connection() as r:
        if not r:
            return JSONResponse(content={
                "id": "",
                "message": "Redis not configured",
            })

        try:
            save_id = str(int(time.time() * 1000))
            name = body.name or f"Search {body.postcodes}"

            full_data = {
                "id": save_id,
                "name": name,
                "postcodes": body.postcodes,
                "date": time.strftime("%Y-%m-%d %H:%M"),
                "count": len(body.places),
                "places": body.places,
            }
            meta = {
                "id": save_id,
                "name": name,
                "postcodes": body.postcodes,
                "date": full_data["date"],
                "count": full_data["count"],
            }

            r.set(f"save:{save_id}", json.dumps(full_data))

            raw_index = r.get("saves:index")
            index: list[dict[str, Any]] = (
                json.loads(raw_index) if raw_index else []
            )
            index.insert(0, meta)
            index = index[:_MAX_SAVED_SEARCHES]
            r.set("saves:index", json.dumps(index))

            return JSONResponse(content={
                "id": save_id,
                "message": "Saved successfully",
            })
        except Exception as exc:
            logger.exception("Failed to save search")
            return JSONResponse(
                status_code=500,
                content={"error": str(exc)},
            )


@app.get("/api/saves")
async def list_or_load_saves(request: Request) -> JSONResponse:
    """List all saved searches, or load one by ID.

    Query parameters:
        id: If provided, load the full saved search with that ID.
            Otherwise return only metadata for all saved searches.

    Args:
        request: The incoming HTTP request.

    Returns:
        JSON with the saved search data or index listing.
    """
    with redis_connection() as r:
        if not r:
            return JSONResponse(content={"saves": []})

        try:
            save_id = request.query_params.get("id")

            if save_id:
                raw = r.get(f"save:{save_id}")
                if not raw:
                    return JSONResponse(
                        status_code=404,
                        content={"error": "Save not found"},
                    )
                return JSONResponse(content=json.loads(raw))

            raw_index = r.get("saves:index")
            saves = json.loads(raw_index) if raw_index else []
            return JSONResponse(content={"saves": saves})
        except Exception as exc:
            logger.exception("Failed to read saves")
            return JSONResponse(content={
                "saves": [],
                "error": str(exc),
            })


@app.delete("/api/saves")
async def delete_save(request: Request) -> JSONResponse:
    """Delete a saved search by ID.

    Query parameters:
        id: The ID of the saved search to delete (required).

    Args:
        request: The incoming HTTP request.

    Returns:
        JSON confirmation or error message.
    """
    with redis_connection() as r:
        if not r:
            return JSONResponse(content={"message": "Redis not configured"})

        try:
            save_id = request.query_params.get("id")
            if not save_id:
                return JSONResponse(
                    status_code=400,
                    content={"error": "Missing id"},
                )

            r.delete(f"save:{save_id}")

            raw_index = r.get("saves:index")
            if raw_index:
                existing = json.loads(raw_index)
                updated = [s for s in existing if s.get("id") != save_id]
                r.set("saves:index", json.dumps(updated))

            return JSONResponse(content={"message": "Deleted"})
        except Exception as exc:
            logger.exception("Failed to delete save %s", save_id)
            return JSONResponse(
                status_code=500,
                content={"error": str(exc)},
            )
