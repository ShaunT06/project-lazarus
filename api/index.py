"""Vercel's Python runtime builds the whole app into one Function from this
file's resolved entrypoint - it just re-exports the real FastAPI app that
lives in app/main.py so nothing about local dev (uvicorn app.main:app)
changes. See vercel.json for the routing that sends every request here.
"""

from app.main import app  # noqa: F401
