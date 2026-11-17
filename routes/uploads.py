"""Serves the two kinds of uploaded photos – only to logged-in staff (CLAUDE rule 9, SRS 7 privacy)."""
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

from common.auth import require_user
from common.uploads import resolve

router = APIRouter(prefix="/app/uploads", tags=["uploads"])


@router.get("/{path:path}")
def get_upload(path: str, user=Depends(require_user)):
    p = resolve(path)
    if not p:
        raise HTTPException(404)
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600", "X-Content-Type-Options": "nosniff"})
