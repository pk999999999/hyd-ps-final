"use client";
import { FormEvent } from "react";

export type ApprovalLead = { id: string; title: string; pitch_hook: string; proposed_idea: string;
  draft_subject: string; draft_body: string; outreach_status: string; emails: string };

export function ApprovalsDesk({ leads, busy, canEmail, liveEmail, onDecide }: {
  leads: ApprovalLead[]; busy: boolean; canEmail: boolean; liveEmail: boolean;
  onDecide: (lead: ApprovalLead, action: "save" | "send" | "reject", subject: string, body: string, basis: string) => void;
}) {
  function submit(event: FormEvent<HTMLFormElement>, lead: ApprovalLead) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const button = (event.nativeEvent as SubmitEvent).submitter as HTMLButtonElement;
    onDecide(lead, button.value as "save" | "send" | "reject",
      String(form.get("subject") || ""), String(form.get("body") || ""),
      String(form.get("permission_basis") || ""));
  }
  return <section className="panel"><div className="panel-head"><h2>Messages awaiting your approval</h2><span className="count">{leads.length} drafts</span></div>
    {leads.length ? <div className="campaign-list">{leads.map(lead => <article key={lead.id}>
      <p className="eyebrow">HUMAN REVIEW REQUIRED · {lead.title}</p><h3>{lead.pitch_hook || "Service idea"}</h3>
      <p>{lead.proposed_idea}</p><p className="muted">Reason: this is an AI-assisted outreach draft. Review its claims and recipient before delivery.</p>
      <form className="review-form" onSubmit={event => submit(event, lead)}>
        <label>Subject<input name="subject" defaultValue={lead.draft_subject} required maxLength={500} /></label>
        <label>Exact message<textarea name="body" defaultValue={lead.draft_body} required rows={7} maxLength={10000} /></label>
        {liveEmail && <label>Basis for contacting this business<textarea name="permission_basis" rows={2} maxLength={1000} placeholder="Describe existing permission or the contact relationship." /><small>A public listing alone is not permission. This is recorded when you send.</small></label>}
        <div className="form-actions"><button className="primary" value="save" disabled={busy}>Approve & save</button>
          <button className="compact" value="send" disabled={busy || !canEmail || !lead.emails}>Approve & send</button>
          <button className="danger" value="reject" formNoValidate disabled={busy}>Reject</button></div>
      </form>
    </article>)}</div> : <div className="empty"><h3>Everything is reviewed.</h3><p>New drafts appear here after discovery and research.</p></div>}
  </section>;
}
