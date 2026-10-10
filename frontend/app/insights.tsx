"use client";

export type Analytics = {
  leads_by_status: Record<string, number>; replies_by_intent: Record<string, number>;
  discovery_jobs_by_status: Record<string, number>; idea_categories: Record<string, number>;
  delivery_outcomes: Record<string, number>; research_completed: number; meetings_booked: number;
  campaigns: number; evidence: number; messages_sent: number; average_lead_score: number;
  rates: Record<string, { numerator: number; denominator: number; percent: number }>;
};
export type SettingsOverview = {
  research_provider: string; tavily_configured: boolean; scraper_configured: boolean;
  demo_mode: boolean; email_provider: string; email_enabled: boolean; daily_email_cap: number;
  sms_enabled: boolean; daily_sms_cap: number; llm_configured: boolean;
  llm_provider: string; llm_model: string; approval_required: boolean;
};
export type MarketingIdea = { id: string; lead_id: string; business_name: string; lead_score: number;
  title: string; problem: string; proposal: string; status: string; evidence: string;
  source_url: string; confidence: string; potential_benefit: string; uncertainty: string;
  implementation: string; suggested_cta: string; draft_subject: string; draft_body: string };

function Breakdown({ title, rows }: { title: string; rows: Record<string, number> }) {
  return <section className="panel panel-body"><h2>{title}</h2>{Object.keys(rows).length ?
    <div className="system-list">{Object.entries(rows).map(([name, value]) => <div key={name}><span>{name.replaceAll("_", " ")}</span><strong>{value}</strong></div>)}</div> :
    <p className="muted">No activity recorded yet.</p>}</section>;
}

export function AnalyticsDesk({ data }: { data: Analytics | null }) {
  if (!data) return <section className="panel panel-body">Loading analytics…</section>;
  return <>
    <section className="stats">{[["Campaigns", data.campaigns], ["Messages accepted", data.messages_sent],
      ["Source observations", data.evidence], ["Meetings booked", data.meetings_booked],
      ["Average lead score", data.average_lead_score]].map(([label, value]) =>
      <article key={label}><span>{label}</span><strong>{value}</strong></article>)}</section>
    <section className="panel panel-body"><h2>Performance rates</h2><p className="muted">Each percentage shows its numerator and denominator. Delivered messages are not counted as conversions.</p>
      <div className="system-list">{Object.entries(data.rates).map(([label, rate]) => <div key={label}>
        <span>{label.replaceAll("_", " ")}</span><strong>{rate.percent}% · {rate.numerator}/{rate.denominator}</strong>
      </div>)}</div><small className="muted">Research coverage: researched leads / all leads. Reply rate: unique inbound leads / unique sent leads. Positive response rate: positive inbound leads / inbound leads. Opt-out and meeting rates: opted-out leads or confirmed meetings / unique sent leads.</small></section>
    <div className="overview-grid"><Breakdown title="Discovery jobs" rows={data.discovery_jobs_by_status} />
      <Breakdown title="Lead pipeline" rows={data.leads_by_status} />
      <Breakdown title="Idea categories" rows={data.idea_categories} />
      <Breakdown title="Delivery outcomes" rows={data.delivery_outcomes} />
      <Breakdown title="Reply intents" rows={data.replies_by_intent} /></div>
  </>;
}

export function IdeasDesk({ ideas, onReview }: { ideas: MarketingIdea[]; onReview: (leadId: string) => void }) {
  return <section className="panel"><div className="panel-head"><h2>Evidence-backed ideas</h2><span className="count">{ideas.length} recent proposals</span></div>
    {ideas.length ? <div className="campaign-list">{ideas.map(idea => <article key={idea.id}>
      <div className="message-heading"><div><p className="eyebrow">{idea.business_name} · {idea.status}</p><h3>{idea.title}</h3></div><span className="status">Score {idea.lead_score}/100</span></div>
      <p><strong>Observed source:</strong> {idea.evidence}</p>
      {idea.source_url && <a href={idea.source_url} target="_blank" rel="noopener noreferrer">Open source</a>}
      <p><strong>Opportunity to check:</strong> {idea.problem}</p><p><strong>Proposed action:</strong> {idea.proposal}</p>
      {idea.potential_benefit && <p><strong>Potential benefit:</strong> {idea.potential_benefit}</p>}
      {idea.implementation && <p><strong>Suggested implementation:</strong> {idea.implementation}</p>}
      {idea.suggested_cta && <p><strong>Suggested next step:</strong> {idea.suggested_cta}</p>}
      <small>Confidence: {idea.confidence}. {idea.uncertainty || "Proposed needs are hypotheses until independently confirmed."}</small>
      {idea.draft_body && <details><summary>Draft outreach</summary><strong>{idea.draft_subject}</strong><p>{idea.draft_body}</p></details>}
      <div className="form-actions"><button className="compact" onClick={() => onReview(idea.lead_id)}>Review idea and message</button></div>
    </article>)}</div> : <div className="empty"><h3>No ideas yet.</h3><p>Start a campaign or draft a discovered lead to generate reviewed proposals.</p></div>}
  </section>;
}

export function SettingsDesk({ data, onStrategy }: { data: SettingsOverview | null; onStrategy: () => void }) {
  if (!data) return <section className="panel panel-body">Loading settings…</section>;
  const rows: [string, string | number][] = [
    ["Reasoning provider", data.llm_provider], ["Model", data.llm_model],
    ["Reasoning key / local model", data.llm_configured ? "configured" : "not configured"],
    ["Research provider", data.research_provider], ["Tavily key", data.tavily_configured ? "configured" : "not configured"],
    ["Maps scraper", data.scraper_configured ? "configured" : "not configured"],
    ["Email", `${data.email_provider} · ${data.email_enabled ? "enabled" : "disabled"}`],
    ["Daily email cap", data.daily_email_cap], ["SMS", data.sms_enabled ? "enabled" : "disabled"],
    ["Daily SMS cap", data.daily_sms_cap], ["Human approval", data.approval_required ? "required" : "disabled"],
    ["Demo mode", data.demo_mode ? "enabled" : "disabled"],
  ];
  return <section className="panel panel-body"><h2>Workspace & deployment settings</h2>
    <div className="system-list">{rows.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
    <p className="muted">Provider keys and delivery limits are set in the deployment environment. Secret values are never shown here. Edit your service, target clients and communication preferences in Memory & strategy.</p>
    <button className="compact" onClick={onStrategy}>Edit strategy and preferences</button>
  </section>;
}
