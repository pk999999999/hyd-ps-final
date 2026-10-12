"use client";
import { FormEvent, useEffect, useRef, useState } from "react";
import {
  ArrowSquareOut,
  ArrowUpRight,
  Check,
  Crosshair,
  Globe,
  MagnifyingGlass,
  MapPin,
  Phone,
  Sparkle,
  SpinnerGap,
  Star,
  WarningCircle,
} from "@phosphor-icons/react";
import { api, User } from "./api";
import { TavilyDiscoveredLead } from "./api/discover-tavily/route";

export type DiscoveryRequest = {
  keywords: string[];
  city: string;
  depth: number;
  scheduled_at?: string;
  latitude?: string;
  longitude?: string;
  location_source: "manual" | "browser";
  location_captured_at?: string;
  radius_m: number;
  prepare_drafts: boolean;
  draft_limit: number;
};

type Position = {
  latitude: number;
  longitude: number;
  accuracy: number;
  capturedAt: number;
  resolvedArea?: string;
};

export function DiscoveryForm({
  busy,
  user,
  onLeadsImported,
  onNavigateToLeads,
}: {
  busy?: boolean;
  hasStrategy?: boolean;
  demoMode?: boolean;
  onDiscover?: (request: DiscoveryRequest) => Promise<void>;
  user?: User;
  onLeadsImported?: () => void;
  onNavigateToLeads?: (leadId?: string) => void;
}) {
  const [source, setSource] = useState<"manual" | "browser">("browser");
  const [position, setPosition] = useState<Position | null>(null);
  const [locating, setLocating] = useState(false);
  const [locationError, setLocationError] = useState("");
  const [manualArea, setManualArea] = useState("Hyderabad, Telangana");

  // User questions requested
  const [service, setService] = useState(
    user?.service_description || "AI receptionist and 24/7 automated appointment booking"
  );
  const [clients, setClients] = useState(
    user?.target_clients || "Hair salons, unisex parlours, dental clinics"
  );
  const [resultLimit, setResultLimit] = useState(20);

  // Discovery execution state
  const [searching, setSearching] = useState(false);
  const [searchError, setSearchError] = useState("");
  const [discoveredLeads, setDiscoveredLeads] = useState<TavilyDiscoveredLead[]>([]);
  const [discoveredLocation, setDiscoveredLocation] = useState("");
  const [importStatus, setImportStatus] = useState("");

  const requestId = useRef(0);
  useEffect(() => () => { requestId.current += 1; }, []);

  // Sync user defaults when user prop loads
  useEffect(() => {
    if (user?.service_description) setService(user.service_description);
    if (user?.target_clients) setClients(user.target_clients);
  }, [user]);

  function manualLocation() {
    requestId.current += 1;
    setSource("manual");
    setPosition(null);
    setLocationError("");
    setLocating(false);
  }

  function locate() {
    const id = ++requestId.current;
    setLocationError("");
    setSource("browser");

    if (typeof window === "undefined" || !navigator.geolocation) {
      setLocationError("This browser cannot share location. Enter an area manually instead.");
      return;
    }

    setLocating(true);
    navigator.geolocation.getCurrentPosition(
      async result => {
        if (id !== requestId.current) return;
        setLocating(false);
        const lat = result.coords.latitude;
        const lon = result.coords.longitude;
        let area = "Hyderabad, Telangana";

        try {
          const res = await fetch(
            `https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=14`,
            { signal: AbortSignal.timeout(5000) }
          );
          if (res.ok) {
            const data = await res.json();
            const a = data.address || {};
            const parts = [
              a.suburb || a.neighbourhood || a.residential || a.road,
              a.city || a.town || "Hyderabad",
            ].filter(Boolean);
            if (parts.length) area = parts.join(", ");
          }
        } catch {
          // fallback to coordinates
          area = `${lat.toFixed(4)}° N, ${lon.toFixed(4)}° E`;
        }

        setPosition({
          latitude: lat,
          longitude: lon,
          accuracy: result.coords.accuracy,
          capturedAt: result.timestamp,
          resolvedArea: area,
        });
      },
      error => {
        if (id !== requestId.current) return;
        setLocating(false);
        setLocationError(
          error.code === 1
            ? "Location permission was denied. Please allow location in your browser settings or enter your area manually."
            : error.code === 3
            ? "Location timed out. Please try again or enter your area manually."
            : "Your device could not determine its location. Please enter your area manually."
        );
      },
      { enableHighAccuracy: true, maximumAge: 0, timeout: 15000 }
    );
  }

  // Run discovery via Tavily API
  async function runTavilyDiscovery(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setSearching(true);
    setSearchError("");
    setImportStatus("");

    try {
      const activeLocation =
        source === "browser" && position
          ? position.resolvedArea || `${position.latitude.toFixed(4)}, ${position.longitude.toFixed(4)}`
          : manualArea || "Hyderabad, Telangana";

      const res = await fetch("/api/discover-tavily", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          service: service.trim(),
          clients: clients.trim(),
          latitude: position?.latitude,
          longitude: position?.longitude,
          location_name: activeLocation,
          limit: resultLimit,
        }),
      });

      const data = await res.json();
      if (!res.ok || !data.success) {
        throw new Error(data.error || "Failed to find businesses from Tavily");
      }

      const leads: TavilyDiscoveredLead[] = data.leads || [];
      setDiscoveredLeads(leads);
      setDiscoveredLocation(data.location || activeLocation);

      // Save user strategy to backend if logged in
      if (user) {
        api("/onboarding/strategy", {
          method: "POST",
          body: JSON.stringify({
            service_description: service.trim(),
            target_clients: clients.trim(),
          }),
        }).catch(() => {});
      }

      // Sync discovered leads into PostgreSQL leads table via /leads/import
      if (data.csv_text && typeof window !== "undefined" && localStorage.getItem("hydps2_token")) {
        try {
          const importResult = await api<{ imported: number }>("/leads/import", {
            method: "POST",
            body: JSON.stringify({ csv_text: data.csv_text }),
          });
          setImportStatus(`Synced ${importResult.imported} new businesses to your Leads pipeline.`);
          if (onLeadsImported) onLeadsImported();
        } catch {
          setImportStatus("Leads ready for review.");
        }
      }
    } catch (err: unknown) {
      setSearchError(err instanceof Error ? err.message : "Error discovering businesses via Tavily.");
    } finally {
      setSearching(false);
    }
  }

  return (
    <>
      <section className="panel tavily-search-panel">
        <div className="panel-head">
          <div>
            <p className="eyebrow">LIVE LOCATION & TAVILY GOOGLE MAPS DISCOVERY</p>
            <h2>Find your next clients near you</h2>
          </div>
          <MapPin size={28} aria-hidden="true" />
        </div>

        <form className="panel-body tavily-form" onSubmit={runTavilyDiscovery}>
          {/* Location Detection Box */}
          <div className="tavily-location-card">
            <div className="tavily-location-header">
              <div>
                <strong>Device Location Settings</strong>
                <p>Fetch your exact live location to discover businesses right in your neighbourhood.</p>
              </div>
              <button
                type="button"
                className="compact"
                disabled={locating || searching || busy}
                onClick={locate}
              >
                {locating ? <SpinnerGap className="spin" /> : <Crosshair />}
                {locating ? "Detecting location…" : position ? "Refresh live location" : "Use my live location"}
              </button>
            </div>

            {position && (
              <div className="location-result">
                <Check size={20} color="var(--green)" />
                <div>
                  <span className="tavily-badge">
                    Live location captured (accuracy ±{Math.round(position.accuracy)}m)
                  </span>
                  <p style={{ margin: "4px 0", fontWeight: 600, fontSize: "13px" }}>
                    {position.resolvedArea}
                  </p>
                  <small className="muted">
                    Coordinates: {position.latitude.toFixed(5)}° N, {position.longitude.toFixed(5)}° E
                  </small>
                </div>
              </div>
            )}

            {locationError && (
              <div className="inline-error" role="alert">
                <WarningCircle /> {locationError}
              </div>
            )}

            {source === "browser" && !position && !locating && (
              <p className="muted" style={{ margin: 0, fontSize: "11px" }}>
                Click <strong>&quot;Use my live location&quot;</strong> to auto-detect your location via browser settings, or switch to manual area.
              </p>
            )}

            <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
              {source === "browser" ? (
                <button
                  type="button"
                  className="compact"
                  style={{ fontSize: "11px" }}
                  onClick={manualLocation}
                >
                  Enter area manually instead
                </button>
              ) : (
                <button
                  type="button"
                  className="compact"
                  style={{ fontSize: "11px" }}
                  onClick={() => {
                    setSource("browser");
                    locate();
                  }}
                >
                  <Crosshair /> Use GPS location
                </button>
              )}
            </div>

            {source === "manual" && (
              <label style={{ marginTop: "10px" }}>
                Area / City
                <input
                  value={manualArea}
                  onChange={e => setManualArea(e.target.value)}
                  placeholder="e.g. Ameerpet, Hyderabad or Banjara Hills, Hyderabad"
                  required
                />
              </label>
            )}
          </div>

          {/* User Details Required: Questions 1 & 2 */}
          <div className="tavily-inputs-grid">
            <label>
              <span className="form-section-title">1. What&apos;s your service?</span>
              <textarea
                value={service}
                onChange={e => setService(e.target.value)}
                required
                rows={2}
                maxLength={1000}
                placeholder="e.g. AI voice receptionist and automated appointment scheduling"
              />
              <small>Your service shapes every customized pitch and hook generated for these businesses.</small>
            </label>

            <label>
              <span className="form-section-title">2. Who are your clients?</span>
              <textarea
                value={clients}
                onChange={e => setClients(e.target.value)}
                required
                rows={2}
                maxLength={1000}
                placeholder="e.g. Hair salons, unisex parlours, dental clinics"
              />
              <small>Target business categories searched live on Google Maps and Tavily.</small>
            </label>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: "16px", flexWrap: "wrap" }}>
            <label style={{ display: "inline-flex", flexDirection: "row", alignItems: "center", gap: "8px" }}>
              <span style={{ fontSize: "12px", color: "var(--muted)" }}>Number of results:</span>
              <select
                value={resultLimit}
                onChange={e => setResultLimit(Number(e.target.value))}
                style={{ width: "auto", minHeight: "36px", padding: "6px 12px" }}
              >
                <option value={15}>Top 15 businesses</option>
                <option value={20}>Top 20 businesses</option>
                <option value={25}>Top 25 businesses</option>
              </select>
            </label>
          </div>

          {searchError && (
            <div className="alert error">
              <WarningCircle /> {searchError}
            </div>
          )}

          {importStatus && (
            <div className="alert success">
              <Check /> {importStatus}
              {onNavigateToLeads && (
                <button
                  type="button"
                  className="compact"
                  style={{ marginLeft: "auto" }}
                  onClick={() => onNavigateToLeads()}
                >
                  View in Leads Pipeline <ArrowUpRight />
                </button>
              )}
            </div>
          )}

          <div className="form-footer">
            <p className="muted" style={{ margin: 0 }}>
              Live Tavily Search + Google Maps intelligence. Finds real businesses with phone, address, and ratings.
            </p>
            <button
              type="submit"
              className="primary"
              disabled={searching || locating || busy || !service.trim() || !clients.trim()}
              style={{ minHeight: "46px", padding: "0 22px", fontSize: "13px" }}
            >
              {searching ? (
                <>
                  <SpinnerGap className="spin" size={18} /> Finding top {resultLimit} businesses…
                </>
              ) : (
                <>
                  <MagnifyingGlass size={18} /> Find top {resultLimit} businesses now
                </>
              )}
            </button>
          </div>
        </form>
      </section>

      {/* Discovered Results: 15–20 Cards Grid */}
      {discoveredLeads.length > 0 && (
        <section className="panel">
          <div className="tavily-results-header">
            <div>
              <span className="eyebrow">TAVILY & GOOGLE MAPS RESULTS</span>
              <h2>Top {discoveredLeads.length} Businesses Found near {discoveredLocation}</h2>
              <p className="tavily-results-summary">
                Targeting &quot;{clients}&quot; · Offering: &quot;{service}&quot;
              </p>
            </div>
            {onNavigateToLeads && (
              <button
                type="button"
                className="primary compact"
                onClick={() => onNavigateToLeads()}
              >
                Open in Leads Pipeline ({discoveredLeads.length}) <ArrowUpRight />
              </button>
            )}
          </div>

          <div className="tavily-grid">
            {discoveredLeads.map((lead, idx) => (
              <article key={lead.id || idx} className="tavily-card">
                <div className="tavily-card-head">
                  <div>
                    <h3 className="tavily-card-title">{lead.title}</h3>
                    <div className="tavily-card-meta">
                      <span className="tavily-badge">{lead.category}</span>
                      {lead.review_rating && (
                        <span>
                          <Star weight="fill" color="#e49e22" size={13} />
                          <strong>{lead.review_rating}</strong> ({lead.review_count || 0})
                        </span>
                      )}
                    </div>
                  </div>
                  <span className="tavily-badge score" title="Completeness score">
                    {lead.lead_score}/100
                  </span>
                </div>

                <div className="tavily-card-address">
                  <MapPin size={16} />
                  <span>{lead.address}</span>
                </div>

                <div className="tavily-card-contact">
                  {lead.phone ? (
                    <a href={`tel:${lead.phone}`} className="tavily-badge phone">
                      <Phone size={13} /> {lead.phone}
                    </a>
                  ) : (
                    <span className="muted" style={{ fontSize: "11px" }}>Phone: Not listed</span>
                  )}
                  {lead.website && (
                    <a
                      href={lead.website}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="tavily-badge"
                    >
                      <Globe size={13} /> Website
                    </a>
                  )}
                </div>

                <div className="tavily-pitch-box">
                  <strong>Personalized Pitch Idea</strong>
                  <p>{lead.proposed_idea}</p>
                </div>

                <div className="tavily-card-actions">
                  {lead.google_maps_url && (
                    <a
                      href={lead.google_maps_url}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="compact"
                    >
                      <ArrowSquareOut size={14} /> Open in Google Maps
                    </a>
                  )}
                  {onNavigateToLeads && (
                    <button
                      type="button"
                      className="compact"
                      onClick={() => onNavigateToLeads(lead.id)}
                    >
                      <Sparkle size={14} /> Draft pitch
                    </button>
                  )}
                </div>
              </article>
            ))}
          </div>
        </section>
      )}
    </>
  );
}
