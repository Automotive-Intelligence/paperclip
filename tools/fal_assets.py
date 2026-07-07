"""
tools/fal_assets.py — fal Assets integration for THE STUDIO.

fal Assets (https://fal.ai/assets) is a searchable, reusable library of generations.
This gives the Studio a compounding, on-brand asset memory:
  * per-brand "approved references" collections (Iris-gated winners),
  * pass those back as reference_image_urls for first-pass on-brand yield,
  * semantic search to reuse instead of regenerate.

API: GET /v1/assets (browse + semantic search), POST /v1/assets/uploads (ingest a
fal-hosted URL), GET/POST /v1/assets/collections. Auth: "Authorization: Key <FAL_KEY>".

Our generations come back as fal-hosted URLs (generate_nano_banana_image -> urls[0]),
so ingest needs no re-upload. For local files, upload_to_fal() hosts them first.
"""
from __future__ import annotations
import os, requests
from typing import Any, Dict, List, Optional

FAL_API = "https://api.fal.ai/v1"


def _key() -> str:
    return os.getenv("FAL_KEY", "").strip()


def fal_assets_ready() -> bool:
    return bool(_key())


def _h(json: bool = False) -> Dict[str, str]:
    h = {"Authorization": f"Key {_key()}"}
    if json:
        h["Content-Type"] = "application/json"
    return h


# ── Collections ──────────────────────────────────────────────────────────────
def list_collections() -> List[Dict[str, Any]]:
    r = requests.get(f"{FAL_API}/assets/collections", headers=_h(), timeout=30)
    r.raise_for_status()
    return r.json().get("collections", [])


def get_or_create_collection(name: str) -> Dict[str, Any]:
    """Idempotent: return the collection named `name`, creating it if absent."""
    for c in list_collections():
        if (c.get("name") or "").lower() == name.lower():
            return c
    r = requests.post(f"{FAL_API}/assets/collections", headers=_h(True),
                      json={"name": name}, timeout=30)
    r.raise_for_status()
    return r.json().get("collection", r.json())


# ── Ingest ───────────────────────────────────────────────────────────────────
def ingest_url(url: str, *, asset_type: str = "image", collection_id: Optional[str] = None,
               prompt: Optional[str] = None, favorite: bool = False,
               tag_ids: Optional[List[str]] = None) -> Dict[str, Any]:
    """Ingest a fal-hosted media URL into the asset library (optionally a collection)."""
    body: Dict[str, Any] = {"url": url, "type": asset_type}
    if collection_id:
        body["collection_id"] = collection_id
    if prompt:
        body["prompt"] = prompt[:2000]
    if favorite:
        body["favorite"] = True
    if tag_ids:
        body["tag_ids"] = tag_ids
    r = requests.post(f"{FAL_API}/assets/uploads", headers=_h(True), json=body, timeout=60)
    r.raise_for_status()
    return r.json()


FAL_STORAGE = "https://rest.alpha.fal.ai/storage/upload/initiate"


def upload_to_fal(path: str, content_type: str = "image/png") -> str:
    """Host a local file on fal's CDN via the storage initiate->PUT flow; return file_url."""
    fn = os.path.basename(path)
    init = requests.post(FAL_STORAGE, headers=_h(True),
                         json={"content_type": content_type, "file_name": fn}, timeout=60)
    init.raise_for_status()
    j = init.json()
    upload_url = j.get("upload_url")
    file_url = j.get("file_url") or j.get("url")
    if not upload_url or not file_url:
        raise RuntimeError(f"fal storage initiate missing urls: {list(j.keys())}")
    with open(path, "rb") as f:
        put = requests.put(upload_url, data=f, headers={"Content-Type": content_type}, timeout=120)
    put.raise_for_status()
    return file_url


# ── Search / reference pull ──────────────────────────────────────────────────
def search_assets(q: Optional[str] = None, *, search_image_url: Optional[str] = None,
                  collection_id: Optional[str] = None, media_type: str = "image",
                  section: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    params: Dict[str, Any] = {"media_type": media_type}
    if q:
        params["q"] = q
    if search_image_url:
        params["search_image_url"] = search_image_url
    if collection_id:
        params["collection_id"] = collection_id
    if section:
        params["section"] = section
    r = requests.get(f"{FAL_API}/assets", headers=_h(), params=params, timeout=30)
    r.raise_for_status()
    return r.json().get("assets", [])[:limit]


def brand_reference_urls(collection_name: str, limit: int = 4) -> List[str]:
    """Top reference image URLs from a brand's approved collection, for reference_image_urls."""
    try:
        col = get_or_create_collection(collection_name)
        cid = col.get("id") or col.get("_id")
        return [a["url"] for a in search_assets(collection_id=cid, limit=limit) if a.get("url")]
    except Exception:
        return []


# ── Studio integration helpers ───────────────────────────────────────────────
def ingest_winner(gen_result: Dict[str, Any], collection_name: str, *,
                  prompt: str = "", favorite: bool = True) -> Optional[str]:
    """Call AFTER Iris gates a generation: file the winning fal URL into the brand collection."""
    urls = (gen_result or {}).get("urls") or []
    if not urls:
        return None
    col = get_or_create_collection(collection_name)
    cid = col.get("id") or col.get("_id")
    res = ingest_url(urls[0], collection_id=cid, prompt=prompt, favorite=favorite)
    return (res.get("asset") or res).get("vector_id") or (res.get("asset") or res).get("url")


# Brand business_key (matches fal_image.py BRAND_PROMPT_STYLES) -> approved-reference collection.
BRAND_COLLECTIONS = {
    "autointelligence": "AvI approved references", "avi": "AvI approved references",
    "wd": "WD approved references", "worshipdigital": "WD approved references",
    "aiphoneguy": "AIPG approved references", "aipg": "AIPG approved references",
    "ae": "AE approved references", "agentempire": "AE approved references",
    "bookd": "Bookd approved references", "book'd": "Bookd approved references",
}


def references_for(business_key: str, limit: int = 4) -> List[str]:
    """Reference image URLs from a brand's approved collection (pass as reference_image_urls)."""
    name = BRAND_COLLECTIONS.get((business_key or "").lower())
    return brand_reference_urls(name, limit) if name else []


def ingest_for(business_key: str, gen_result: Dict[str, Any], *,
               prompt: str = "", favorite: bool = True) -> Optional[str]:
    """File an Iris-gated generation into the brand's approved collection by business_key."""
    name = BRAND_COLLECTIONS.get((business_key or "").lower())
    return ingest_winner(gen_result, name, prompt=prompt, favorite=favorite) if name else None
