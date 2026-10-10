"""Dispatch, poll and import Google Maps scraper jobs."""
import csv
import io
import json
import math
import asyncio
from datetime import datetime, timezone
import httpx
from sqlalchemy import select, or_, and_
from app.models.models import Lead, Job, User, WorkItem, CampaignLead
from app.core.config import get_settings

GEOCODER_URL = "https://nominatim.openstreetmap.org/search"
GEOCODER_USER_AGENT = "hyd-ps2/1.0 (https://github.com/atharv20s/hyd)"
_geocode_lock = asyncio.Lock()
_last_geocode_at = 0.0


async def geocode_city(city: str) -> tuple[str, str]:
    """Resolve a campaign city before submitting the scraper's required coordinates."""
    global _last_geocode_at
    if not city or len(city) > 255:
        raise ValueError("A valid city is required for discovery.")
    async with _geocode_lock:
        # Nominatim's public service asks clients to stay below one request per second.
        loop = asyncio.get_running_loop()
        await asyncio.sleep(max(0, 1.0 - (loop.time() - _last_geocode_at)))
        _last_geocode_at = loop.time()
        async with httpx.AsyncClient(timeout=15) as client:
            response = await client.get(GEOCODER_URL, params={"format": "json", "limit": 1, "q": city},
                                        headers={"User-Agent": GEOCODER_USER_AGENT})
            response.raise_for_status()
            places = response.json()
    if not isinstance(places, list) or not places:
        raise ValueError(f"Could not locate {city!r}. Enter a more specific city or use a browser location.")
    try:
        latitude, longitude = float(places[0]["lat"]), float(places[0]["lon"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise ValueError("The geocoder returned invalid coordinates.") from exc
    if not (math.isfinite(latitude) and math.isfinite(longitude) and -90 <= latitude <= 90 and -180 <= longitude <= 180):
        raise ValueError("The geocoder returned invalid coordinates.")
    return str(latitude), str(longitude)


def distance_m(lat1, lon1, lat2, lon2):
    """Great-circle distance, including the antimeridian; no geocoding key needed."""
    a, b = math.radians(lat1), math.radians(lat2)
    h = math.sin((b - a) / 2) ** 2 + math.cos(a) * math.cos(b) * math.sin(math.radians(lon2 - lon1) / 2) ** 2
    return 6371008.8 * 2 * math.asin(math.sqrt(min(1, max(0, h))))


class ScraperService:
    def __init__(self, db_session):
        self.session = db_session
        self.base_url = get_settings().scraper_base_url.rstrip("/")

    async def ingest_csv_to_leads(self, user_id, csv_filepath, job_id=None):
        with open(csv_filepath, encoding="utf-8-sig", errors="replace") as stream:
            return await self.ingest_csv(user_id, stream.read(), job_id)

    async def ingest_csv(self, user_id, content, job_id=None, *, location_job=None, commit=True):
        # Imports are serialized per operator so simultaneous CSV uploads / workers
        # cannot race the duplicate checks or overwrite suppression state.
        await self.session.execute(select(User.id).where(User.id == user_id).with_for_update())
        count = 0
        self.excluded_count = 0
        self.imported_ids = []
        reader = csv.DictReader(io.StringIO(content.lstrip("\ufeff")))
        if not reader.fieldnames or "title" not in reader.fieldnames:
            raise ValueError("CSV must contain a title column.")
        for row in reader:
            title = (row.get("title") or "").strip()
            if not title:
                continue
            def value(name):
                return (row.get(name) or "").strip()
            def number(name, conversion=float):
                try:
                    result = conversion(value(name)) if value(name) else None
                    return result if result is None or math.isfinite(result) else None
                except (ValueError, OverflowError):
                    return None
            latitude, longitude = number("latitude"), number("longitude")
            if location_job and location_job.latitude != "" and location_job.longitude != "":
                if (latitude is None or longitude is None or not -90 <= latitude <= 90 or not -180 <= longitude <= 180
                    or distance_m(float(location_job.latitude), float(location_job.longitude), latitude, longitude) > location_job.radius_m):
                    self.excluded_count += 1
                    continue
            place_id, phone, address = value("place_id"), value("phone"), value("address")
            identities = [and_(Lead.title == title, Lead.address == address)]
            if place_id:
                identities.append(Lead.place_id == place_id)
            if phone:
                identities.append(Lead.phone == phone)
            existing = await self.session.scalar(select(Lead.id).where(Lead.user_id == user_id, or_(*identities)).limit(1))
            if existing:
                if location_job and location_job.campaign_id and not await self.session.get(CampaignLead, (location_job.campaign_id, existing)):
                    self.session.add(CampaignLead(campaign_id=location_job.campaign_id, lead_id=existing))
                    await self.session.flush()
                continue
            emails = value("emails").replace(";", ",")
            try:
                parsed = json.loads(emails)
                if isinstance(parsed, list):
                    emails = ",".join(str(item) for item in parsed)
            except (ValueError, TypeError):
                pass
            lead = Lead(user_id=user_id, job_id=job_id, title=title, category=value("category"), address=address,
                phone=phone, emails=emails, website=value("website"), review_rating=number("review_rating"),
                review_count=number("review_count", int), latitude=latitude, longitude=longitude,
                place_id=place_id, google_maps_url=value("link"), raw_data=dict(row), outreach_status="new")
            from app.services.communication_service import is_suppressed
            if await is_suppressed(self.session, lead):
                lead.opted_out = True
                lead.status = "opted_out"
            self.session.add(lead)
            await self.session.flush()
            self.imported_ids.append(lead.id)
            if location_job and location_job.campaign_id:
                self.session.add(CampaignLead(campaign_id=location_job.campaign_id, lead_id=lead.id))
            count += 1
        if commit:
            await self.session.commit()
        return count

    async def dispatch(self, job):
        if get_settings().demo_mode:
            job.scraper_job_id = f"demo:{job.id}"
            job.status = "running"
            job.error_message = None
            await self.session.commit()
            return
        keywords = json.loads(job.keywords) if isinstance(job.keywords, str) else job.keywords
        queries = keywords if job.location_source == "browser" else [f"{word} {job.city}".strip() for word in keywords]
        if not job.latitude or not job.longitude:
            try:
                job.latitude, job.longitude = await geocode_city(job.city)
            except httpx.HTTPError as exc:
                raise ValueError("City lookup is unavailable. Retry discovery or use a browser location.") from exc
        payload = {"name": f"hyd-{job.id}", "keywords": queries,
                   "lang": "en", "zoom": 15, "depth": job.depth, "email": True, "max_time": 600,
                   "fast_mode": False, "radius": job.radius_m or 5000,
                   "lat": str(job.latitude), "lon": str(job.longitude)}
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(f"{self.base_url}/api/v1/jobs", json=payload)
            response.raise_for_status()
        data = response.json()
        scraper_id = data.get("id") or data.get("ID")
        if not scraper_id:
            raise ValueError("The scraper did not return a job ID.")
        job.scraper_job_id = str(scraper_id)
        job.status = "running"
        job.error_message = None
        await self.session.commit()

    async def poll(self, job):
        if job.status == "completed":
            return job.status
        if job.scraper_job_id and job.scraper_job_id.startswith("demo:"):
            await self._complete_job(job, self._demo_csv(job))
            return job.status
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.get(f"{self.base_url}/api/v1/jobs/{job.scraper_job_id}")
            response.raise_for_status()
            data = response.json()
            state = str(data.get("Status") or data.get("status") or "running").lower()
            if state in ("ok", "completed", "finished"):
                results = await client.get(f"{self.base_url}/api/v1/jobs/{job.scraper_job_id}/download")
                results.raise_for_status()
                await self._complete_job(job, results.content.decode("utf-8-sig", errors="replace"))
                return job.status
            elif state in ("failed", "error", "cancelled", "canceled"):
                job.status = "failed"
                job.error_message = "Scraping failed. Try a smaller search or retry later."
            await self.session.commit()
            return job.status

    async def _complete_job(self, job, csv_content):
        # Commit imports, counters and follow-on tasks together. Retrying a
        # completed job must never reset its counts or enqueue more drafts.
        await self.session.execute(select(User.id).where(User.id == job.user_id).with_for_update())
        await self.session.refresh(job, with_for_update=True)
        if job.status == "completed":
            return
        job.lead_count = await self.ingest_csv(job.user_id, csv_content, job.id, location_job=job, commit=False)
        job.excluded_count = self.excluded_count
        job.drafts_queued = 0
        if job.prepare_drafts and self.imported_ids:
            user = await self.session.get(User, job.user_id)
            if user and user.service_description.strip():
                leads = (await self.session.scalars(select(Lead).where(Lead.id.in_(self.imported_ids), Lead.opted_out.is_(False))
                    .order_by(Lead.created_at, Lead.id).limit(job.draft_limit))).all()
                for lead in leads:
                    lead.status = "queued"
                    self.session.add(WorkItem(user_id=job.user_id, kind="draft", payload={"lead_id": str(lead.id),
                        **({"campaign_id": str(job.campaign_id)} if job.campaign_id else {})}))
                job.drafts_queued = len(leads)
        job.status = "completed"
        job.completed_at = datetime.now(timezone.utc)
        await self.session.commit()
        from app.services.communication_service import publish
        await publish(str(job.user_id), "scrape_completed", str(job.id))

    @staticmethod
    def _demo_csv(job):
        keywords = json.loads(job.keywords) if isinstance(job.keywords, str) else job.keywords
        category = str((keywords or ["local business"])[0])[:80]
        city = str(job.city or "Hyderabad")[:80]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=["title", "category", "address", "emails", "website",
            "review_rating", "review_count", "place_id", "latitude", "longitude", "demo_fixture"])
        writer.writeheader()
        for number, (rating, reviews) in enumerate(((4.2, 31), (4.5, 68), (4.0, 23)), start=1):
            writer.writerow({"title": f"[Demo] {category.title()} {number}", "category": category,
                "address": f"Sample district {number}, {city}", "emails": f"demo{number}@example.com",
                "website": f"https://example.com/demo-{number}", "review_rating": rating,
                "review_count": reviews, "place_id": f"demo:{job.id}:{number}",
                "latitude": job.latitude or "", "longitude": job.longitude or "", "demo_fixture": "true"})
        return stream.getvalue()
