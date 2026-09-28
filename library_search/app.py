"""FastAPI app: the search page (GET /), JSON search (GET /api/search) and inventory reload (POST /api/reload)."""
from __future__ import annotations

import html
import secrets
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse

from .core import Reranker, build_index

PAGE = Path(__file__).with_name("page.html")
MAX_QUERY = 200  # characters; a reader's search is a few words, not a document


def create_app(inventory_path, synonyms_path=None, rerank: Reranker | None = None, notice: str = "",
               reload_key: str | None = None) -> FastAPI:
    """notice: a banner shown on the page (e.g. "SAMPLE DATA"). reload_key: when set, POST /api/reload needs
    the same value in the X-Reload-Key header."""
    app = FastAPI(title="Library search", docs_url=None, redoc_url=None)
    app.state.index = build_index(inventory_path, synonyms_path)
    page = PAGE.read_text(encoding="utf-8").replace("{{NOTICE}}", html.escape(notice))

    @app.get("/", response_class=HTMLResponse)
    def home() -> str:
        return page

    @app.get("/api/search")
    def search(q: str = Query("", max_length=MAX_QUERY), limit: int = Query(10, ge=1, le=50)) -> dict:
        index = app.state.index
        return {**index.search(q, limit=limit, rerank=rerank).to_dict(), "inventory": index.stats()}

    @app.post("/api/reload")
    def reload(x_reload_key: str | None = Header(default=None)) -> dict:
        if reload_key and not secrets.compare_digest(x_reload_key or "", reload_key):
            raise HTTPException(status_code=401, detail="missing or wrong X-Reload-Key")
        try:
            app.state.index = build_index(inventory_path, synonyms_path)
        except Exception as exc:  # missing column, corrupt .xlsx, file locked by Excel: search keeps running
            raise HTTPException(status_code=422,
                                detail=f"reload failed, previous inventory kept: {type(exc).__name__}: {exc}")
        return {"status": "reloaded", "inventory": app.state.index.stats()}

    return app
