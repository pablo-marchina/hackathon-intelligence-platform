import type {Metadata} from 'next';
import Link from 'next/link';
import {notFound} from 'next/navigation';
import {EventStateControl} from '../../../components/EventStateControl';
import {getHackathon} from '../../../lib/api';

const fmt=(v?:string|null)=>v?new Intl.DateTimeFormat('en',{dateStyle:'medium',timeStyle:'short'}).format(new Date(v)):'TBA';
const label=(v:unknown)=>String(v??'').replaceAll('_',' ').toLowerCase();

export async function generateMetadata({params}:{params:Promise<{slug:string}>}):Promise<Metadata>{
  const{slug}=await params;
  try{const e=await getHackathon(slug);return{title:e.name,description:e.short_description??`Verified hackathon details for ${e.name}`,alternates:{canonical:`/hackathons/${e.slug}`}}}
  catch{return{title:'Hackathon'}}
}

export default async function HackathonPage({params}:{params:Promise<{slug:string}>}){
  const{slug}=await params;let e;try{e=await getHackathon(slug)}catch{notFound()}
  const reg=e.registration_url||e.application_url;
  const location=[e.location?.venue,e.location?.city,e.location?.state_region,e.location?.country].filter(Boolean).join(', ')|| (e.format==='ONLINE'?'Online':'TBA');
  const jsonLd={'@context':'https://schema.org','@type':'Event',name:e.name,startDate:e.start_at,endDate:e.end_at,eventAttendanceMode:e.format==='ONLINE'?'https://schema.org/OnlineEventAttendanceMode':e.format==='HYBRID'?'https://schema.org/MixedEventAttendanceMode':'https://schema.org/OfflineEventAttendanceMode',location:e.location?.city?{'@type':'Place',name:location}:undefined,url:e.official_url||undefined};
  return <main>
    <script type="application/ld+json" dangerouslySetInnerHTML={{__html:JSON.stringify(jsonLd)}}/>
    <div className="detail-hero"><div><p className="eyebrow">{e.event_type} · {e.status}</p><h1>{e.name}</h1>{e.subtitle?<p className="lead">{e.subtitle}</p>:null}<p><strong>{label(e.format)}</strong> · {location}</p><div className="tag-row">{e.tracks.map(t=><span className="tag" key={t.id}>{t.name}</span>)}{e.technologies.map(t=><span className="tag" key={t.id}>{t.name}</span>)}</div></div><aside className="detail-panel"><div className="data-list"><div><span className="eyebrow">Starts</span><strong>{fmt(e.start_at)}</strong></div><div><span className="eyebrow">Ends</span><strong>{fmt(e.end_at)}</strong></div><div><span className="eyebrow">Registration deadline</span><strong>{fmt(e.registration_close_at)}</strong></div><div><span className="eyebrow">Data confidence</span><strong>{e.quality_score?.toFixed(1)??'—'}/100 · {e.source_count} sources</strong></div></div>{reg?<p><a className="button" href={reg}>Registration / application ↗</a></p>:null}{e.official_url?<p><a href={e.official_url}>Canonical official event page ↗</a></p>:null}<EventStateControl slug={e.slug}/></aside></div>
    {e.full_description?<section className="section"><h2>About</h2><p>{e.full_description}</p></section>:null}
    <section className="section"><h2>Eligibility & teams</h2>{e.eligibility?<pre>{JSON.stringify(e.eligibility,null,2)}</pre>:<p className="muted">No structured eligibility verified yet.</p>}{e.requirements.length?<><h3>Requirements</h3><ul>{e.requirements.map((r,i)=><li key={i}><strong>{String(r.name??r.requirement_type??'Requirement')}</strong>{r.description?` — ${String(r.description)}`:''}{r.required===false?' (optional)':''}</li>)}</ul></>:null}</section>
    {e.prizes.length?<section className="section"><h2>Prizes</h2><div className="data-list">{e.prizes.map((p,i)=><div key={i}><strong>{String(p.title??p.placement??'Prize')}</strong><span>{p.normalized_usd_amount?` · $${Number(p.normalized_usd_amount).toLocaleString()} USD`:p.non_cash_description?` · ${String(p.non_cash_description)}`:''}</span></div>)}</div></section>:null}
    {e.phases.length?<section className="section"><h2>Timeline & phases</h2><div className="series-list">{e.phases.map((p,i)=><div className="series-row" key={i}><span>{fmt(p.starts_at)}</span><strong>{String(p.name??p.phase_type??'Phase')}</strong><span>{fmt(p.ends_at)}</span></div>)}</div></section>:null}
    <section className="section"><h2>Organizations & sponsors</h2><div className="tag-row">{e.organizations.map(o=><Link className="tag" href={`/organizations/${o.slug}`} key={`${o.id}-${o.role}`}>{o.name} · {o.role}</Link>)}</div>{e.series_slug?<p>Part of <Link href={`/series/${e.series_slug}`}>{e.series_name}</Link>.</p>:null}</section>
    {e.resources.length?<section className="section"><h2>Resources</h2><ul>{e.resources.map((r,i)=><li key={i}>{r.url?<a href={String(r.url)}>{String(r.name??r.resource_type??'Resource')} ↗</a>:<strong>{String(r.name??r.resource_type??'Resource')}</strong>}{r.description?` — ${String(r.description)}`:''}</li>)}</ul></section>:null}
    {e.judging.length?<section className="section"><h2>Judging</h2><ul>{e.judging.map((j,i)=><li key={i}><strong>{String(j.name??'Criterion')}</strong>{j.weight!=null?` · ${String(j.weight)}%`:''}{j.description?` — ${String(j.description)}`:''}</li>)}</ul></section>:null}
    {e.links.length?<section className="section"><h2>Verified links</h2><div className="tag-row">{e.links.map((l,i)=><a className="tag" href={l.url} key={`${l.url}-${i}`}>{label(l.link_type)}{l.is_official?' · official':''} ↗</a>)}</div></section>:null}
    <section className="section"><h2>Freshness, changes & evidence</h2><p>Last verified: <strong>{fmt(e.last_verified_at)}</strong> · {e.source_count} sources.</p>{e.recent_changes.length?<div className="data-list">{e.recent_changes.map((c,i)=><div key={i}><strong>{c.field_path}</strong><span> · {c.change_type} · {fmt(c.detected_at)}</span></div>)}</div>:<p className="muted">No important recent canonical changes.</p>}<div className="data-list">{e.evidence.slice(0,20).map((x,i)=><div key={i}><strong>{x.field_path}</strong> <span className="provenance">{x.source_name||'Source'} · confidence {x.confidence??'—'}</span></div>)}</div></section>
  </main>
}
