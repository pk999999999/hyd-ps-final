"""Bounded, source-attributed lead research and deterministic opportunity scoring."""
import ipaddress
import asyncio
import hashlib
import re
import socket
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
from sqlalchemy import select

from app.core.config import get_settings
from app.models.models import LeadEvidence, ResearchRun, Job, Campaign, User


class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hidden = 0
        self.parts = []

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style", "noscript"}:
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in {"script", "style", "noscript"}:
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data)


def public_url(value):
    """Reject local/private destinations before a direct public website request."""
    parsed = urlparse(value or "")
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
        return False
    try:
        if parsed.port and parsed.port not in {80, 443}:
            return False
        infos = socket.getaddrinfo(parsed.hostname, parsed.port or (443 if parsed.scheme == "https" else 80))
        return bool(infos) and all(ipaddress.ip_address(info[4][0]).is_global for info in infos)
    except (OSError, ValueError):
        return False


async def website_excerpt(url):
    if not await asyncio.to_thread(public_url, url):
        return ""
    try:
        async with httpx.AsyncClient(timeout=8, follow_redirects=False, trust_env=False) as client:
            async with client.stream("GET", url, headers={"User-Agent": "hyd-ps2-research/1.0"}) as response:
                response.raise_for_status()
                if response.status_code != 200:
                    return ""
                if "text/html" not in response.headers.get("content-type", ""):
                    return ""
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > 300_000:
                        break
                    chunks.append(chunk)
        parser = _VisibleText()
        parser.feed(b"".join(chunks).decode("utf-8", errors="replace"))
        return re.sub(r"\s+", " ", " ".join(parser.parts)).strip()[:1200]
    except (httpx.HTTPError, UnicodeError, ValueError):
        return ""


async def tavily_results(lead):
    key = get_settings().tavily_api_key
    if not key:
        return []
    query = " ".join(part for part in (lead.title, lead.address, lead.category) if part)[:300]
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.post("https://api.tavily.com/search",
                headers={"Authorization": f"Bearer {key}"},
                json={"query": query, "search_depth": "basic", "max_results": 3,
                      "include_answer": False, "include_raw_content": False, "safe_search": True})
            response.raise_for_status()
        return response.json().get("results", [])
    except (httpx.HTTPError, ValueError):
        return []


def score_breakdown(lead, evidence, target_clients=""):
    """Auditable signals; a low rating is never interpreted as a need."""
    kinds = {item.source_kind for item in evidence}
    settings = get_settings()
    category = {word.rstrip("s") for word in re.findall(r"[a-z]{4,}", (lead.category or "").lower())
                if word not in {"local", "business", "service"}}
    target = {word.rstrip("s") for word in re.findall(r"[a-z]{4,}", (target_clients or "").lower())}
    return {"business_record": settings.lead_score_record_weight,
            "email_available": settings.lead_score_email_weight if lead.email else 0,
            "phone_available": settings.lead_score_phone_weight if lead.phone else 0,
            "website_listed": settings.lead_score_website_weight if lead.website else 0,
            "maps_identity": settings.lead_score_maps_weight if lead.google_maps_url or lead.place_id else 0,
            "website_observed": settings.lead_score_observed_website_weight if "website" in kinds else 0,
            "matched_search_source": settings.lead_score_matched_search_weight if "search" in kinds else 0,
            "category_and_address": settings.lead_score_profile_weight if lead.category and lead.address else 0,
            "target_category_match": settings.lead_score_target_fit_weight if category.intersection(target) else 0}


def score_lead(lead, evidence, target_clients=""):
    return min(sum(score_breakdown(lead, evidence, target_clients).values()), 100)


def matches_business(lead, result):
    """Avoid attributing similarly named search hits to the wrong business."""
    title = re.sub(r"[^a-z0-9]+", " ", (lead.title or "").lower()).strip()
    haystack = re.sub(r"[^a-z0-9]+", " ", f"{result.get('title', '')} {result.get('content', '')}".lower())
    words = [word for word in title.split() if len(word) >= 4]
    return bool(words) and all(word in haystack.split() for word in words[:3])


async def research_lead(db, lead, *, force=False):
    """Idempotently collect listing, owned-site and search snippets with provenance."""
    if not force and lead.research_status == "researched":
        existing = (await db.scalars(select(LeadEvidence).where(LeadEvidence.lead_id == lead.id))).all()
        if existing:
            return [{"id": str(row.id), "title": row.title, "excerpt": row.excerpt,
                     "source_kind": row.source_kind, "source_url": row.source_url,
                     "confidence": row.confidence} for row in existing]
    run = ResearchRun(user_id=lead.user_id, lead_id=lead.id)
    db.add(run)
    await db.flush()
    candidates = []
    listing = "; ".join(filter(None, (lead.category, lead.address,
        f"{lead.review_rating} stars across {lead.review_count} reviews" if lead.review_rating is not None and lead.review_count is not None else "")))
    if listing:
        maps_url = lead.google_maps_url or ""
        candidates.append(("maps", maps_url if maps_url.startswith(("https://", "http://")) else "",
                           "Maps listing", listing, "observed"))
    if get_settings().demo_mode:
        candidates = [("demo", "", "Synthetic sample listing", listing or lead.title, "synthetic")]
    else:
        excerpt = await website_excerpt(lead.website) if lead.website else ""
        if excerpt:
            candidates.append(("website", lead.website, "Business website", excerpt, "observed"))
        for result in await tavily_results(lead):
            url, content = str(result.get("url") or ""), str(result.get("content") or "")
            if url.startswith(("https://", "http://")) and content.strip() and matches_business(lead, result):
                candidates.append(("search", url[:1000], str(result.get("title") or "Search result")[:500],
                    content[:1200], "third_party"))
    existing = (await db.scalars(select(LeadEvidence).where(LeadEvidence.lead_id == lead.id))).all()
    keys = {row.source_key for row in existing}
    for kind, url, title, excerpt, confidence in candidates:
        key = hashlib.sha256(f"{kind}\0{url}\0{title}".encode()).hexdigest()
        if key not in keys:
            db.add(LeadEvidence(lead_id=lead.id, source_kind=kind, source_key=key, source_url=url,
                title=title, excerpt=excerpt, confidence=confidence))
            keys.add(key)
    await db.flush()
    evidence = (await db.scalars(select(LeadEvidence).where(LeadEvidence.lead_id == lead.id))).all()
    job = await db.get(Job, lead.job_id) if lead.job_id else None
    campaign = await db.get(Campaign, job.campaign_id) if job and job.campaign_id else None
    user = await db.get(User, lead.user_id)
    target_clients = campaign.target_clients if campaign else user.target_clients if user else ""
    lead.lead_score_factors = score_breakdown(lead, evidence, target_clients)
    lead.lead_score = min(sum(lead.lead_score_factors.values()), 100)
    lead.research_status = "researched" if evidence else "no_sources"
    run.status = lead.research_status
    run.sources_found = len(evidence)
    run.completed_at = datetime.now(timezone.utc)
    await db.flush()
    return [{"id": str(row.id), "title": row.title, "excerpt": row.excerpt,
             "source_kind": row.source_kind, "source_url": row.source_url, "confidence": row.confidence}
            for row in evidence]
