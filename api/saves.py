"""
/api/saves — CRUD for saved search results using Redis.
GET  /api/saves         — list all saved searches (metadata only)
POST /api/saves         — save a search result
DELETE /api/saves?id=X  — delete a saved search
GET /api/saves?id=X     — load a specific saved search (full data)
"""
from __future__ import annotations
import json
import os
import time
from typing import Any
import redis
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
app = FastAPI()

def _get_redis():
    url = os.environ.get("REDIS_URL", "")
    if not url:
        return None
    try:
        return redis.from_url(url, decode_responses=True, socket_timeout=5)
    except Exception:
        return None

class SaveRequest(BaseModel):
    name: str = ""
    postcodes: str = ""
    places: list[dict[str, Any]] = []

@app.post("/api/saves")
async def save_search(body: SaveRequest):
    r = _get_redis()
    if not r:
        return JSONResponse(content={"id": "", "message": "Redis not configured"})
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
        raw = r.get("saves:index")
        existing: list[dict] = []
        if raw:
            existing = json.loads(raw)
        existing.insert(0, meta)
        existing = existing[:50]
        r.set("saves:index", json.dumps(existing))
        return JSONResponse(content={"id": save_id, "message": "Saved successfully"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    finally:
        r.close()

@app.get("/api/saves")
async def list_or_load_saves(request: Request):
    r = _get_redis()
    if not r:
        return JSONResponse(content={"saves": []})
    try:
        save_id = request.query_params.get("id")
        if save_id:
            raw = r.get(f"save:{save_id}")
            if not raw:
                return JSONResponse(status_code=404, content={"error": "Save not found"})
            return JSONResponse(content=json.loads(raw))
        else:
            raw = r.get("saves:index")
            if not raw:
                return JSONResponse(content={"saves": []})
            return JSONResponse(content={"saves": json.loads(raw)})
    except Exception as exc:
        return JSONResponse(content={"saves": [], "error": str(exc)})
    finally:
        r.close()

@app.delete("/api/saves")
async def delete_save(request: Request):
    r = _get_redis()
    if not r:
        return JSONResponse(content={"message": "Redis not configured"})
    try:
        save_id = request.query_params.get("id")
        if not save_id:
            return JSONResponse(status_code=400, content={"error": "Missing id"})
        r.delete(f"save:{save_id}")
        raw = r.get("saves:index")
        if raw:
            existing = json.loads(raw)
            updated = [s for s in existing if s.get("id") != save_id]
            r.set("saves:index", json.dumps(updated))
        return JSONResponse(content={"message": "Deleted"})
    except Exception as exc:
        return JSONResponse(status_code=500, content={"error": str(exc)})
    finally:
        r.close()
