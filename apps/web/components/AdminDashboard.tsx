'use client';

import {useCallback, useEffect, useState} from 'react';
import {browserApiBase} from '../lib/api';

type Role='VIEWER'|'OPERATOR'|'ADMIN';
type Identity={role:Role;actor_kind:'USER_SESSION'|'API_KEY';csrf_token:string|null};
type Source={id:string;name:string;domain:string;health:string;priority:number;active:boolean;last_attempt_at:string|null;last_success_at:string|null;last_error_at:string|null;next_due_at:string|null;recrawl_priority:number|null;interval_seconds:number|null;plan_reason:string|null;lease_until:string|null};
type Review={id:string;queue_type:string;entity_type:string;entity_id:string;severity:string;state:'OPEN'|'IN_REVIEW'|'RESOLVED'|'DISMISSED';payload:unknown;created_at:string;assigned_to_user_id:string|null};
type Quality={scope_type:string;scope_id:string;overall_score:number|null;measured_at:string};
type Audit={id:string;action:string;actor_kind:string;actor_user_id:string|null;entity_type:string;entity_id:string;reason:string;created_at:string;request_id:string|null};
type Coverage={as_of:string;recent_window_days:number;source_counts:{registered_in_database:number;active_in_database:number;approved_and_active:number;recent_success:number};catalog:{unique_active:number;upcoming_by_date:number;ongoing_by_date:number;confirmed_registration_window:number;unknown_registration_close:number;with_official_url:number;with_start_end:number;with_primary_location:number;inactive_or_retired:number};sources:Array<{source_id:string;name:string;permission_status:string|null;active:boolean;recent_success:boolean;source_refs:number;attributed_unique_active:number}>};
type Tab='coverage'|'sources'|'reviews'|'quality'|'audit';

function readableDate(value:string|null|undefined):string{
  if(!value)return 'Never';
  const date=new Date(value);
  return Number.isNaN(date.getTime())?'Unknown':date.toLocaleString();
}

class AdminFetchError extends Error{constructor(public readonly status:number,message:string){super(message);}}

async function adminRequest<T>(path:string,options?:{method:'PATCH'|'POST';csrf:string;payload:unknown}):Promise<T>{
  const response=await fetch(`${browserApiBase()}/admin${path}`,{
    method:options?.method??'GET',credentials:'include',cache:'no-store',
    headers:options?{'Content-Type':'application/json','X-CSRF-Token':options.csrf}:{},
    body:options?JSON.stringify(options.payload):undefined,
  });
  if(!response.ok){
    const error=await response.json().catch(()=>null) as {detail?:unknown}|null;
    const detail=typeof error?.detail==='string'?error.detail:`HTTP ${response.status}`;
    throw new AdminFetchError(response.status,detail);
  }
  return await response.json() as T;
}

function ErrorMessage({error}:{error:string}){return <p className="warning" role="alert">{error}</p>;}

export function AdminDashboard(){
  const [identity,setIdentity]=useState<Identity|null>(null);
  const [status,setStatus]=useState<'loading'|'ready'|'denied'|'error'>('loading');
  const [error,setError]=useState('');
  const [tab,setTab]=useState<Tab>('coverage');
  const [sources,setSources]=useState<Source[]>([]);
  const [coverage,setCoverage]=useState<Coverage|null>(null);
  const [reviews,setReviews]=useState<Review[]>([]);
  const [quality,setQuality]=useState<Quality[]>([]);
  const [audit,setAudit]=useState<Audit[]>([]);
  const [busy,setBusy]=useState<string|null>(null);
  const [reason,setReason]=useState('');
  const [notice,setNotice]=useState('');

  const refresh=useCallback(async()=>{
    // Parallel reads are safe: every API request carries the same user session.
    const [nextCoverage,nextSources,nextReviews,nextQuality,nextAudit]=await Promise.all([
      adminRequest<Coverage>('/coverage'),
      adminRequest<Source[]>('/source-health'),
      adminRequest<Review[]>('/review-queue?limit=100'),
      adminRequest<Quality[]>('/quality?limit=100'),
      adminRequest<Audit[]>('/audit?limit=50'),
    ]);
    setCoverage(nextCoverage);setSources(nextSources);setReviews(nextReviews);setQuality(nextQuality);setAudit(nextAudit);
  },[]);

  useEffect(()=>{
    let cancelled=false;
    (async()=>{
      try{
        const me=await adminRequest<Identity>('/me');
        if(cancelled)return;
        if(me.actor_kind!=='USER_SESSION')throw new Error('Use an authorized browser session to open the dashboard.');
        await refresh();
        if(!cancelled){setIdentity(me);setStatus('ready');}
      }catch(e){if(cancelled)return;
        if(e instanceof AdminFetchError&&(e.status===401||e.status===403||e.status===503))setStatus('denied');
        else setStatus('error');
        setError(e instanceof Error?e.message:'Unable to load admin dashboard');
      }
    })();
    return()=>{cancelled=true;};
  },[refresh]);

  const canOperate=identity?.role==='OPERATOR'||identity?.role==='ADMIN';
  const canReindex=identity?.role==='ADMIN';

  async function action(id:string,method:'PATCH'|'POST',path:string,payload:Record<string,unknown>){
    if(!identity?.csrf_token||!canOperate){setError('Operator access and CSRF protection are required.');return;}
    const explanation=reason.trim();
    if(explanation.length<8){setError('Enter an explanation of at least 8 characters before changing data.');return;}
    setBusy(id);setError('');setNotice('');
    try{
      const result=await adminRequest<Record<string,unknown>>(path,{method,csrf:identity.csrf_token,payload:{...payload,reason:explanation}});
      await refresh();
      setReason('');
      setNotice(result.changed===false?'No change needed.':`Action completed and recorded in the audit log.`);
    }catch(e){setError(e instanceof Error?e.message:'Action failed');}
    finally{setBusy(null);}
  }

  if(status==='loading')return <section className="outage" role="status">Checking access to operations…</section>;
  if(status==='denied')return <section className="outage"><h2>Access restricted</h2><p>Sign in with an account explicitly granted a VIEWER, OPERATOR or ADMIN role.</p><ErrorMessage error={error}/></section>;
  if(status==='error')return <section className="outage"><h2>Operations unavailable</h2><ErrorMessage error={error}/></section>;

  return <section className="admin-dashboard">
    <div className="admin-toolbar">
      <strong>Access: {identity?.role}</strong>
      <button type="button" disabled={busy!==null} onClick={async()=>{setError('');try{await refresh();setNotice('Data refreshed.');}catch(e){setError(e instanceof Error?e.message:'Refresh failed');}}}>Refresh</button>
    </div>
    <div className="admin-metrics" aria-label="Operations overview">
      <div><strong>{sources.length}</strong><span>Sources</span></div>
      <div><strong>{sources.filter(s=>!s.active).length}</strong><span>Paused</span></div>
      <div><strong>{reviews.length}</strong><span>Open reviews</span></div>
      <div><strong>{sources.filter(s=>s.health==='BROKEN'||s.health==='DEGRADED').length}</strong><span>Degraded</span></div>
    </div>
    <div className="admin-tabs" role="tablist" aria-label="Operations section">
      {(['coverage','sources','reviews','quality','audit'] as const).map(t=><button key={t} type="button" role="tab" aria-selected={tab===t} className={tab===t?'selected':''} onClick={()=>{setTab(t);setError('');setNotice('');}}>{({coverage:'Catalog coverage',sources:'Sources',reviews:'Review queue',quality:'Quality',audit:'Audit trail'} as const)[t]}</button>)}
    </div>
    {canOperate&&tab!=='quality'&&tab!=='audit'&&tab!=='coverage'&&<label className="admin-reason">Reason for change (required, min. 8 characters)
      <textarea rows={2} maxLength={500} value={reason} onChange={e=>setReason(e.target.value)} placeholder="Explain the operational decision"/>
    </label>}
    {notice&&<p role="status" className="admin-notice">{notice}</p>}
    {error&&<ErrorMessage error={error}/>}
    {tab==='coverage'&&<div className="admin-list">
      {!coverage?<p className="muted">No database coverage snapshot available.</p>:<>
        <p className="muted">Database snapshot at {readableDate(coverage.as_of)}. Counts are not historical W2 totals.</p>
        <div className="admin-stats">
          <div><strong>{coverage.catalog.unique_active}</strong><span>Unique active hackathons</span></div>
          <div><strong>{coverage.catalog.upcoming_by_date}</strong><span>Upcoming by date</span></div>
          <div><strong>{coverage.catalog.confirmed_registration_window}</strong><span>Confirmed registration windows</span></div>
          <div><strong>{coverage.source_counts.recent_success}</strong><span>Sources successful in last {coverage.recent_window_days} days</span></div>
        </div>
        <p className="muted">{coverage.catalog.unknown_registration_close} events have no known registration deadline. Unknown is not equivalent to open. Per-source attribution overlaps.</p>
        {coverage.sources.map(item=><article className="event-card admin-row" key={item.source_id}>
          <div><strong>{item.name}</strong><p className="muted">{item.permission_status??'UNKNOWN'} · {item.active?'Active':'Paused'} · {item.recent_success?'Recent successful run':'No recent successful run'}</p></div>
          <div><strong>{item.attributed_unique_active}</strong><span className="muted"> attributed canonicals · {item.source_refs} refs</span></div>
        </article>)}
      </>}
    </div>}
    {tab==='sources'&&<div className="admin-list">
      {sources.length===0&&<p className="muted">No sources found.</p>}
      {sources.map(source=><article className="event-card admin-row" key={source.id}>
        <div><strong>{source.name}</strong><p className="muted">{source.domain} · Health: {source.health} · Priority: {source.priority}</p>
          <p className="muted">Last success: {readableDate(source.last_success_at)} · Last attempt: {readableDate(source.last_attempt_at)}</p>
          <p className="muted">Next recrawl: {source.active?readableDate(source.next_due_at):'Paused'} · Cadence: {source.interval_seconds?`${Math.round(source.interval_seconds/3600)}h`:'Not planned'} · Score: {source.recrawl_priority??'—'}</p>
          {source.plan_reason&&<p className="muted">Schedule: {source.plan_reason}</p>}
          {source.lease_until&&<p className="muted">Worker lease expires: {readableDate(source.lease_until)}</p>}
          {source.last_error_at&&<p className="warning">Last error: {readableDate(source.last_error_at)}</p>}
        </div>
        <div className="admin-actions"><span className={source.active?'admin-state':'admin-state paused'}>{source.active?'Active':'Paused'}</span>
          {canOperate&&<button type="button" disabled={busy!==null||reason.trim().length<8} onClick={()=>action(source.id,'PATCH',`/sources/${encodeURIComponent(source.id)}/active`,{active:!source.active})}>{busy===source.id?'Updating…':source.active?'Pause source':'Resume source'}</button>}
        </div>
      </article>)}
    </div>}
    {tab==='reviews'&&<div className="admin-list">
      {reviews.length===0&&<p className="muted">No pending reviews.</p>}
      {reviews.map(review=><article className="event-card admin-row" key={review.id}>
        <div><strong>{review.queue_type}</strong> <span className="tag">{review.severity}</span>
          <p className="muted">{review.entity_type} · {review.state} · Created {readableDate(review.created_at)}</p>
          <details><summary>Evidence</summary><pre className="admin-evidence">{JSON.stringify(review.payload,null,2)}</pre></details>
        </div>
        {canOperate&&<div className="admin-actions">
          {review.state==='OPEN'&&<button type="button" disabled={busy!==null||reason.trim().length<8} onClick={()=>action(review.id,'PATCH',`/review-queue/${encodeURIComponent(review.id)}`,{state:'IN_REVIEW'})}>Start review</button>}
          {(['RESOLVED','DISMISSED'] as const).map(state=><button type="button" key={state} disabled={busy!==null||reason.trim().length<8} onClick={()=>action(review.id,'PATCH',`/review-queue/${encodeURIComponent(review.id)}`,{state})}>{state==='RESOLVED'?'Resolve':'Dismiss'}</button>)}
        </div>}
      </article>)}
    </div>}
    {tab==='quality'&&<div className="admin-list">
      {quality.length===0&&<p className="muted">No quality snapshots available.</p>}
      {quality.map(q=><div className="event-card admin-row" key={`${q.scope_type}/${q.scope_id}`}><div><strong>{q.scope_type}: {q.scope_id}</strong><p className="muted">Measured {readableDate(q.measured_at)}</p></div><strong>{q.overall_score??'—'}</strong></div>)}
    </div>}
    {tab==='audit'&&<div className="admin-list">
      {audit.length===0&&<p className="muted">No administrative changes recorded.</p>}
      {audit.map(event=><article className="event-card" key={event.id}><strong>{event.action}</strong><p>{event.reason}</p><p className="muted">{event.actor_kind} · {event.actor_user_id??'service'} · {readableDate(event.created_at)}</p><p className="muted">{event.entity_type}: {event.entity_id}</p></article>)}
    </div>}
    {canReindex&&<details className="admin-maintenance"><summary>Maintenance · Admin only</summary>
      <p className="muted">Rebuild the hackathon search index. Provide a reason before proceeding.</p>
      <label>Maintenance reason<textarea value={reason} onChange={e=>setReason(e.target.value)} rows={2} maxLength={500}/></label>
      <button type="button" disabled={busy!==null||reason.trim().length<8} onClick={()=>action('reindex','POST','/search/reindex',{})}>Rebuild search index</button>
    </details>}
  </section>;
}
