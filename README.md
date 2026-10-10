# HYD PS2 — Made for you. Made for హైదరాబాద్.

A Hyderabad-focused growth desk with separate operator and invited-client portals.
English UI, warm local styling, Charminar line art, actual dependency status and
human-approved AI outreach.

## What is implemented

- JWT signup/login/refresh; client access is bound to a signed seven-day business invitation.
- PostgreSQL stores accounts, lead previews, durable tasks, conversations, send reservations and permanent contact suppression.
- MongoDB stores operator decisions, style preferences, chat/voice history and client service requests. Redis caches history and broadcasts tenant-specific WebSocket events.
- Discovery runs the Google Maps scraper with email extraction. CSV import keeps all raw columns, skips duplicates, and preserves opt-outs.
- **Use my location** requests browser permission on click, captures a fresh position and shows its accuracy. Nearby jobs pass coordinates to the live scraper and exclude businesses outside the selected 0.5–50 km radius or without usable coordinates. There is no background location tracking.
- Optional post-discovery drafting queues a bounded number of new, eligible leads (default 10) for Scout → Critic → Communication and human review. This uses LLM credits; it never sends messages or makes calls.
- A LangGraph Research → Scout → Critic → Communication graph stores source-attributed observations and proposed ideas, scores contact and source completeness, then interrupts at human review. PostgreSQL checkpoints preserve drafts through approval, edits or rejection. No draft automatically sends.
- Campaign planning/discovery and inbound reply classification run as separate LangGraph workflows. SQL work items retain retries and polling between invocations.
- Campaign timelines, idea sources, analytics and deployment settings are available in the operator dashboard.
- Separate authenticated ElevenLabs WebRTC operator and client assistants. The operator can read real neighbourhood pipeline counts; the client collects service, location, time and contact, then saves a pending intake only after consent.
- A communication hub handles email replies and client chat. Operators can draft/edit/approve replies, answer clients in their portal, and confirm/decline service requests.
- Signed, durable Resend webhooks; retry-safe event processing; per-operator WebSocket channels with a ten-second polling fallback.
- STOP, unsubscribe, opt-out, not interested, complaints and bounces suppress future contact. Suppression is checked at import, queue, worker and send boundaries, including a different business record sharing that email address.
- Email sends include AI-use disclosure and signed one-click unsubscribe GET/POST links. Provider idempotency, per-operator SQL reservations and daily caps prevent duplicate outreach. An uncertain timeout stays pending instead of automatically retrying.
- Learning means stored corrections and few-shot decisions included in future prompts. It is not model fine-tuning.

## Run the stack

Keep the existing `.env`; do not overwrite your keys with the example.
For a fresh checkout, copy `.env.example` to `.env`, generate a strong `JWT_SECRET`,
set the database password and provide one reasoning provider key. For example, set
`LLM_PROVIDER=groq` and `GROQ_API_KEY`, or use `LLM_PROVIDER=compatible` with
`LLM_MODEL` and `LLM_BASE_URL` for a local OpenAI-compatible server. The selected
provider is never silently replaced when it fails. Optional voice, SMS, Telegram
and Resend credentials can remain blank.

For development without Compose, run PostgreSQL, MongoDB and Redis locally and
point `DATABASE_URL`, `MONGO_URL` and `REDIS_URL` at those services. Install the
backend dependencies from `backend/requirements.txt`, then start the API and
worker in separate terminals from `backend`:

```powershell
python -m uvicorn app.main:app --reload --port 8000
python -m app.worker
```

From `frontend`, run `npm install` and `npm run dev`. The API and worker apply
Alembic migrations at startup. Live discovery additionally needs the inspected
Google Maps scraper API at `SCRAPER_BASE_URL`; demo campaigns avoid it. Local
email previews need MailHog when `EMAIL_PROVIDER=smtp`; set `SMTP_HOST=localhost`
for a host-installed MailHog. These services are not
bundled into the Python or Node processes.

```powershell
docker compose up -d --build
docker compose ps
```

Open the app at [localhost:3000](http://localhost:3000).
API documentation: [localhost:8000/docs](http://localhost:8000/docs).

Local ports are frontend 3000, API gateway 8000, PostgreSQL 5432, MongoDB 27017,
Redis 6380 and scraper 8081. The Redis/scraper host ports avoid this machine's
existing services. Container-to-container connections use the standard internal ports.
Database and scraper volumes are persistent; do not run `docker compose down -v`
unless you explicitly intend to erase their data.

The scraper container uses the same pinned `gosom/google-maps-scraper:v1.18.1`
web API as the [Google Maps Scraper Kit](https://github.com/Mahanaicoach/google-maps-scraper-kit).
The app submits a bounded job to `/api/v1/jobs`, polls its status, downloads its
CSV, and normalizes records. The kit's Claude commands and standalone CLI are
not required in the running stack. A city-only campaign resolves coordinates
through OpenStreetMap Nominatim before submitting the job; browser-location
searches use the coordinates supplied by the user. City lookup and live discovery
therefore need internet access. The scraper is bound to localhost on the host and
has no public authentication; do not expose its port publicly. Respect source
terms and applicable contact-data rules.

The campaign graph plans and dispatches durable SQL work items. Each selected lead then runs
the LangGraph `research → scout → critic → draft → human_review` nodes. `human_review`
interrupts and saves a PostgreSQL checkpoint; approval resumes that graph without
regenerating the proposal. PostgreSQL lead records and the work queue remain the
source of truth, and the email service rechecks suppression, approval and daily
limits immediately before delivery. Incoming replies run later as separate
durable webhook tasks through a separate conversation graph; they do not keep the lead graph open.

Research uses public business websites and, when `TAVILY_API_KEY` is set, the
[Tavily Search API](https://docs.tavily.com/documentation/api-reference/endpoint/search).
Search hits must match the business name; all snippets retain a source URL and
confidence label. The score is a transparent completeness measure, not a claim
that a business has a problem or will convert. Its factors include a simple
category-to-target match and can be adjusted with `LEAD_SCORE_*_WEIGHT` settings.
Website requests reject private
network addresses and redirects. Research failures leave listing evidence usable.

`/health` is liveness only. `/ready` checks SQL and MongoDB; authenticated
`/api/v1/system/status` also reports Redis, worker heartbeat and provider configuration.
A configured provider is not a claim that a browser microphone or a phone number was tested.

## Use the workspace

1. Create an operator account, then save your actual service and target clients in **Memory & strategy**.
2. Discover an area, choose **Use my location** and a radius, or import a CSV containing a `title` column. Location requires localhost/HTTPS and browser permission; denied access leaves manual area search available. A location older than five minutes must be refreshed before submission. Scheduled jobs retain the point shared at submission, not your future location.
3. Select businesses in **Lead pipeline**, queue drafts, then review each selected service idea and message.
4. **Approve & save** does not send. **Approve & send** explicitly dispatches only when sending is enabled.
5. Open a business preview to start its voice demo or create a client invitation.
6. Invited clients get a separate portal with their own chat, voice assistant and where/when request form.
7. Use **Communication** for client questions, email replies and pending confirmations.
8. Add style preferences or feedback at a checkpoint. Later drafts use those rules and decision examples.

## Complete offline demo (no provider keys required)

Copy `.env.example` to `.env` and set the following values. Demo mode deliberately
requires separate SQL and MongoDB names and local MailHog delivery, so sample
records cannot be mistaken for a live campaign:

```dotenv
APP_ENV=demo
DEMO_MODE=true
POSTGRES_DB=hydps2_demo
DATABASE_URL=postgresql+asyncpg://hydps2:CHANGE_ME@localhost:5432/hydps2_demo
MONGO_DB=hydps2_memory_demo
EMAIL_PROVIDER=smtp
EMAIL_ENABLED=false
```

Set `POSTGRES_PASSWORD` and the URL password to the same value and generate a
strong `JWT_SECRET`. Leave LLM and scraper provider keys blank. Start in a
separate Compose project (stop any stack already using the same host ports):

```powershell
docker compose -p hydps2-demo up -d --build
```

Create an operator, then submit the two campaign answers. Demo planning produces
three `[Demo]` sample businesses, deterministic hypothetical ideas and drafts,
and runs the same LangGraph interruption and approval path as live mode. The
dashboard displays a persistent demo banner. Approved messages go only to local
MailHog at [localhost:8025](http://localhost:8025). Use the authenticated reply
simulator described below for interest and STOP examples. The demo campaign flow
does not call live Maps, Nominatim, LLM or email providers. The demo data uses reserved
example addresses and is stored in the separate demo databases.

## Local email demo (no domain required)

The default is `EMAIL_PROVIDER=smtp`, with `EMAIL_ENABLED=false` (live Resend
disabled). Approved messages are delivered only to MailHog, not to the recipient's
real mailbox. Open [MailHog](http://localhost:8025) to inspect them. SMTP is
restricted to local MailHog on port 1025. Existing approval, cap, reservation and
opt-out checks still apply. Do not interpret a dry-run delivery as a real conversion.

1. Import/discover leads and generate drafts. Approve selected drafts in the app.
2. Use **Approve & send** to deliver to MailHog, or authenticated
   `POST /api/v1/communications/outreach` with `{"lead_ids":["LEAD_UUID"]}`.
   Only approved, unsuppressed drafts are queued; repeated submissions are deduplicated.
3. In [API docs](http://localhost:8000/docs), authorize with your operator token.
   `GET /api/v1/communications/simulation-address/{lead_id}` returns the signed
   demo alias (`to`) and business sender (`from`).
4. `POST /api/v1/communications/simulate-reply` accepts the Resend-shaped payload
   below. Copy the alias and sender returned above. Reuse `email_id` only when
   testing duplicates; choose a new ID for each new reply.

```json
{
  "type": "email.received",
  "data": {
    "email_id": "demo-reply-001",
    "from": "business@example.com",
    "to": ["SIGNED_ALIAS_FROM_SIMULATION_ADDRESS"],
    "subject": "Re: Your proposal",
    "text": "Interested. Can we schedule a demo?"
  }
}
```

Simulation requires operator authentication, ownership, a matching sender,
`APP_ENV=development` or `APP_ENV=demo`, and SMTP dry-run mode. It bypasses only the provider body
fetch, never ownership/suppression checks. Both live and simulated events use the
same durable queue and reply handler. View drafts in **Communication**, actual
interested replies at `/api/v1/communications/interested`, notifications at
`/api/v1/notifications` and meeting proposals at `/api/v1/meetings`.
Meetings require human confirmation; replies require review before sending.
Simulate `STOP` to verify permanent suppression. Provider failure leaves webhook
work scheduled for up to three attempts, then failed for operator review.
An uncertain send is not retried automatically.

Operator alerts are stored locally in `/api/v1/notifications`. Telegram delivery
defaults to dry-run too; its HTTP contract is tested with mocks. To enable generic
installation alerts later, configure `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` and
`TELEGRAM_ENABLED=true`. No business message bodies are forwarded to the shared
Telegram destination. Failed alert tasks remain visible for review; uncertain
Telegram deliveries are not automatically resent.

## Going live

Live mode needs a verified domain. No domain or public URL is needed for the demo.

1. [Verify a sending domain in Resend](https://resend.com/docs/dashboard/domains/introduction)
   and add its DNS records. Set `EMAIL_FROM` to an authorized sender on that domain.
2. [Configure receiving](https://resend.com/docs/dashboard/receiving/introduction),
   then set `RECEIVING_DOMAIN` to the actual receiving domain (without `https://`).
3. Deploy the API on HTTPS or start a working tunnel. Set `PUBLIC_BASE_URL` to
   the real API origin; never use the placeholder `YOUR-PUBLIC-BACKEND`.
4. Register `/api/v1/webhooks/resend` under that origin in Resend, selecting
   `email.received`, `email.delivered`, `email.bounced`, `email.complained`,
   `email.suppressed`. Set its signing secret privately in `.env`.
5. Set `EMAIL_PROVIDER=resend` and `EMAIL_ENABLED=true` only after verifying
   sender identity, reply routing, public opt-out access and the approved recipients.
   Rebuild/recreate API and worker containers; start with your own consenting test recipient.
6. Keep human approval and opt-outs enabled. Never send the whole scraped list
   indiscriminately. No cold AI calls are made by this email workflow.

## Live voice configuration

Use `ELEVENLABS_API_KEY`, `ELEVENLABS_AGENT_ID` (client) and
`ELEVENLABS_OPERATOR_AGENT_ID` (operator). To provision/update both agents
and their matching client tools using the API:

```powershell
.\.venv\Scripts\python.exe backend/scripts/configure_voice.py
```

Copy the returned non-secret agent IDs to `.env`, then recreate API/worker containers.
The script requires provider write access, preserves existing voice choices and enables authentication.
Browser clients receive short-lived conversation tokens, never the API key.
Microphone/WebRTC requires localhost or HTTPS.

For post-call transcripts configure the deployed HTTPS endpoint
`/api/v1/voice/webhook` and `ELEVENLABS_WEBHOOK_SECRET` in the ElevenLabs dashboard.
PSTN inbound/outbound calling still requires a purchased/connected Twilio or SIP number
and an explicit tenant/business routing configuration. The app does not automatically
dial leads or claim that telephone calls have been connected.

Phone calling is out of the current build scope. A normal Jio SIM or a typed phone
number is not an ElevenLabs telephony connection. No personal number has been
registered with a provider by this application. Existing browser voice demos remain available.

## WhatsApp / SMS status

WhatsApp remains manual only: a click-to-chat link cannot receive replies into this
app. Automatic WhatsApp replies require the official WhatsApp Business Platform,
number onboarding, authenticated webhooks and a public HTTPS endpoint.

SMS replies can use a paired Android phone and its own SIM through Textbee. The
backend verifies Textbee's HMAC signature, deduplicates events, matches the inbound
number to one unique business record, and stores it in Communication for human review.
`STOP`, opt-out, and refusal language permanently suppress that business's email and
phone contacts. A reviewed inbox reply is the only SMS action available; initial or
bulk SMS outreach is intentionally not implemented.

To activate it, pair the Android phone in Textbee, deploy this API to public HTTPS,
then create a Textbee `MESSAGE_RECEIVED` webhook pointing to:

```text
https://YOUR_API_HOST/api/v1/webhooks/textbee
```

Set its 32-byte signing secret as `TEXTBEE_WEBHOOK_SECRET`, optionally set
`TEXTBEE_DEVICE_ID`, and only then enable `SMS_ENABLED=true`. The phone must remain
powered and connected. SMS delivery and commercial-message compliance remain the
operator's responsibility.

## Email and incoming replies

Real outreach is disabled by default. Provider credentials alone do not establish
a verified sending domain or a receiving inbox.

Configure in Resend:

- Verify your sending domain and set `EMAIL_FROM` on that domain.
- Enable receiving on a dedicated domain/subdomain and set `RECEIVING_DOMAIN`.
  Outgoing Reply-To aliases are signed and business-specific; inbound senders must match the lead's address.
- Set a publicly reachable HTTPS `PUBLIC_BASE_URL`, correct `FRONTEND_URL` and allowed `CORS_ORIGINS`.
- Create a webhook at `PUBLIC_BASE_URL/api/v1/webhooks/resend`, subscribing to
  `email.received`, `email.delivered`, `email.bounced`, `email.complained` and `email.suppressed`.
  Store its signing secret in `RESEND_WEBHOOK_SECRET`.
- After testing with an address you own, set `EMAIL_ENABLED=true` and recreate the containers.

Unmatched webhooks are ignored, invalid signatures rejected, duplicates deduplicated.
Replies are classified but never automatically sent. Failed events can be retried from
Background tasks. Signed unsubscribe requests work without a login.

Disclosure, opt-outs and caps are implemented; they do not by themselves establish
legal permission to send marketing. Operators remain responsible for recipient eligibility,
applicable rules, sender identification and accurate content.

## Scale and deploy

```powershell
docker compose up -d --scale backend=2 --scale worker=3
```

The Nginx gateway resolves backend replicas through Docker DNS and proxies WebSockets.
Workers share a PostgreSQL leased queue (`FOR UPDATE SKIP LOCKED`) with separate async
sessions and bounded concurrency. Per-operator reservations, suppression and Redis pubsub
are shared across replicas. This is not a load-test or high-availability certification.

Put production behind HTTPS, keep database/cache ports private, use strong secrets,
set explicit CORS origins and a stable public URL, and back up SQL/Mongo volumes.
For another deployment hostname set `NEXT_PUBLIC_WS_URL` at frontend build time to
the externally reachable API origin (`wss://...`). The frontend HTTP proxy targets the
Docker gateway in the production image; local development targets 127.0.0.1:8000.

Startup applies Alembic revisions under a PostgreSQL advisory lock, preserving
existing data. The baseline adopts older databases and creates missing research
tables/columns. Take backups before upgrading an existing deployment. Startup
fails if a migration fails, so an unknown schema is never served.
To apply migrations manually with the same `DATABASE_URL`, run
`cd backend; alembic upgrade head`.

## Verify

```powershell
python -m pip install -r backend/requirements-dev.txt
python -m pytest backend/tests -q
cd frontend
npm ci
npm run typecheck
npm run build
npm audit --omit=dev
```

`backend/scripts/smoke_providers.py` checks the live reasoning pipeline, both authenticated
voice agents and Resend domain status without sending an email or contacting a lead.

Tests cover authentication, tenant isolation, import deduplication, background tasks,
human approval/memory, signed webhooks, refusal suppression after reimport, provider
timeout reservations, client portal replies, appointment intake and WebSocket authorization.
The focused graph tests also verify node order, interruption, review resumption and
the city-to-scraper request contract without using paid APIs.
Browser call/audio QA and real email delivery need a browser/microphone and a verified
sending domain respectively; API checks are not substitutes for those tests.

## Current boundaries

Demo mode provides deterministic planning, sample discovery, synthetic evidence,
ideas, drafts, MailHog delivery and simulated replies without paid services.
Website and search snippets are source-attributed observations; proposed business
problems remain hypotheses for operator review. Live provider and browser behavior
requires configured services and credentials to verify end to end.

## Secret handling

Provider keys belong only in the ignored root `.env`, never in `NEXT_PUBLIC_*`
or committed source. Rotate keys that have been pasted into shared chats.

Reasoning requests use a modest `LLM_MAX_OUTPUT_TOKENS=640` ceiling, minimal
reasoning effort and price-sorted provider routing by default.
OpenRouter can reject even valid keys with HTTP 402 when available credits cannot
cover the requested ceiling. Add credits for sustained use; the app never fabricates
a successful draft when the provider rejects it.
