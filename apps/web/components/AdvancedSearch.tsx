'use client';

import {useEffect, useState} from 'react';
import {EventCard} from './EventCard';
import {browserApiBase} from '../lib/api';
import type {HackathonPage} from '../lib/types';

type FilterField = {key: string; type: 'text' | 'country' | 'boolean' | 'number' | 'datetime'; operators: string[]; description: string};
type Catalog = {schema_version: string; fields: FilterField[]; max_depth: number; max_nodes: number};
type Rule = {id: number; field: string; op: string; value: string; negative: boolean};
type Group = 'and' | 'or';
type Expression = Record<string, unknown>;
const labels: Record<string,string> = {
  eq: 'equals', ne: 'does not equal', in: 'one of', gt: 'greater than',
  gte: 'at least', lt: 'less than', lte: 'at most', between: 'between',
  contains: 'contains text', is_known: 'has information', is_unknown: 'is unknown',
};
const valueNotRequired = new Set(['is_known','is_unknown']);
const title = (value:string) => value.replaceAll('_',' ').replace(/\b\w/g,c=>c.toUpperCase());
const emptyRule = (id:number, field:FilterField):Rule => ({
  id, field:field.key, op:field.operators[0], value:'', negative:false,
});

function converted(field:FilterField, op:string, raw:string): unknown {
  if (valueNotRequired.has(op)) return undefined;
  if (!raw.trim()) throw new Error(`Fill in the value for ${title(field.key)}`);
  const convert = (entry:string): string | number | boolean => {
    const value = entry.trim();
    if (!value) throw new Error('Filter values cannot be blank');
    if (field.type==='boolean') {
      if (value!=='true' && value!=='false') throw new Error('Choose true or false');
      return value==='true';
    }
    if (field.type==='number') {
      const number=Number(value);
      if (!Number.isFinite(number)) throw new Error('Enter a valid numeric value');
      return number;
    }
    return field.type==='country'?value.toUpperCase():value;
  };
  if (op==='in' || op==='between') {
    const parts=raw.split(',').map(part=>convert(part));
    if (op==='between' && parts.length!==2) throw new Error('Enter two values separated by a comma');
    if (op==='in' && (parts.length===0 || parts.length>20)) throw new Error('Enter 1 to 20 values');
    return parts;
  }
  return convert(raw);
}

export function AdvancedSearch() {
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [rules,setRules]=useState<Rule[]>([]);
  const [group,setGroup]=useState<Group>('and');
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [page,setPage]=useState<HackathonPage|null>(null);
  const [activeRequest,setActiveRequest]=useState<Record<string,unknown>|null>(null);

  useEffect(()=>{
    const controller=new AbortController();
    fetch(`${browserApiBase()}/v1/hackathons/filter-fields`,{signal:controller.signal})
      .then(async res=>{
        if(!res.ok) throw new Error('Filter definitions are not available');
        return res.json() as Promise<Catalog>;
      }).then(data=>{
        setCatalog(data);
        if(data.fields.length) setRules([emptyRule(1,data.fields.find(f=>f.key==='format')??data.fields[0])]);
      }).catch(err=>{
        if(!controller.signal.aborted) setError(err instanceof Error?err.message:'Unable to load filter fields');
      });
    return ()=>controller.abort();
  },[]);

  const update=(id:number,change:Partial<Rule>)=>setRules(old=>old.map(rule=>{
    if(rule.id!==id)return rule;
    if(change.field && change.field!==rule.field){
      const field=catalog?.fields.find(item=>item.key===change.field);
      return field?{...rule,field:field.key,op:field.operators[0],value:'',negative:false}:rule;
    }
    return {...rule,...change};
  }));

  const add=()=>{
    const field=catalog?.fields[0];
    if(!field || rules.length>=12)return;
    setRules(old=>[...old,emptyRule(Math.max(0,...old.map(r=>r.id))+1,field)]);
  };

  const buildRequest=():Record<string,unknown>=>{
    if(!catalog)throw new Error('Filter catalog has not loaded');
    if(!rules.length)throw new Error('Add at least one condition');
    const conditions=rules.map(rule=>{
      const field=catalog.fields.find(f=>f.key===rule.field);
      if(!field || !field.operators.includes(rule.op))throw new Error('Unsupported field or operator');
      const leaf:Expression={field:rule.field,op:rule.op};
      if(!valueNotRequired.has(rule.op))leaf.value=converted(field,rule.op,rule.value);
      return rule.negative?{not:leaf}:leaf;
    });
    return {limit:30,sort:'deadline',expression:conditions.length===1?conditions[0]:{[group]:conditions}};
  };

  async function execute(request:Record<string,unknown>,more=false) {
    setBusy(true);
    setError('');
    try {
      const response=await fetch(`${browserApiBase()}/v1/hackathons/query`,{
        method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request),
      });
      if(!response.ok){
        if(response.status===422)throw new Error('Filter combination is not valid. Review values and timezones.');
        throw new Error(`Search is unavailable (HTTP ${response.status})`);
      }
      const data=await response.json() as HackathonPage;
      setPage(old=>more && old?{...data,items:[...old.items,...data.items]}:data);
      setActiveRequest(request);
    }catch(err){
      setError(err instanceof Error?err.message:'Search failed');
    }finally{
      setBusy(false);
    }
  }
  const submit=()=>{
    try{
      const request=buildRequest();
      setPage(null);
      void execute(request);
    }catch(err){
      setError(err instanceof Error?err.message:'Invalid search');
    }
  };

  return <div className="advanced-search-layout">
    <section className="filter-panel advanced-builder" aria-label="Advanced hackathon filters">
      <h2>Build your search</h2>
      <p className="muted">Combine verified fields and relationships. Unknown data is not treated as false.</p>
      <label className="advanced-group-label">
        Match conditions
        <select value={group} onChange={e=>setGroup(e.target.value as Group)}>
          <option value="and">ALL conditions (AND)</option>
          <option value="or">ANY condition (OR)</option>
        </select>
      </label>
      {rules.map((rule,index)=>{
        const field=catalog?.fields.find(item=>item.key===rule.field);
        const requiresValue=!valueNotRequired.has(rule.op);
        return <fieldset className="advanced-rule" key={rule.id}>
          <legend>Condition {index+1}</legend>
          <label>Attribute
            <select value={rule.field} onChange={e=>update(rule.id,{field:e.target.value})}>
              {catalog?.fields.map(item=><option key={item.key} value={item.key}>{title(item.key)}</option>)}
            </select>
          </label>
          <label>Operator
            <select value={rule.op} onChange={e=>update(rule.id,{op:e.target.value,value:''})}>
              {field?.operators.map(op=><option key={op} value={op}>{labels[op]??op}</option>)}
            </select>
          </label>
          {requiresValue&&<label>Value
            {field?.type==='boolean'?
              <select value={rule.value} onChange={e=>update(rule.id,{value:e.target.value})}>
                <option value="">Choose…</option><option value="true">Yes</option><option value="false">No</option>
              </select>:
              <input
                type={field?.type==='number' && rule.op!=='in' && rule.op!=='between'?'number':'text'}
                inputMode={field?.type==='number'?'decimal':undefined}
                value={rule.value}
                placeholder={rule.op==='between'?'1000, 5000':rule.op==='in'?'ONLINE, HYBRID':field?.type==='datetime'?'2026-11-01T00:00:00-03:00':'Enter a value'}
                onChange={e=>update(rule.id,{value:e.target.value})}
              />
            }
          </label>}
          {field?.description&&<small className="muted">{field.description}</small>}
          <div className="advanced-rule-actions">
            <label><input type="checkbox" checked={rule.negative} onChange={e=>update(rule.id,{negative:e.target.checked})}/> Negate</label>
            <button type="button" className="secondary" onClick={()=>setRules(old=>old.filter(x=>x.id!==rule.id))}>Remove</button>
          </div>
        </fieldset>;
      })}
      <div className="advanced-builder-actions">
        <button type="button" className="secondary" disabled={!catalog || rules.length>=12} onClick={add}>+ Add condition</button>
        <button type="button" disabled={!catalog || busy} onClick={submit}>{busy?'Searching…':'Find hackathons'}</button>
      </div>
      {error&&<p role="alert" className="warning">{error}</p>}
      {!catalog&&!error&&<p className="muted">Loading filter definitions…</p>}
    </section>
    <section aria-live="polite">
      <h2>Matching hackathons</h2>
      {!page?<p className="muted">Choose filters and run a search.</p>:
        <>
          <p className="muted">{page.items.length} hackathon(s) loaded. Results may have additional pages.</p>
          {page.items.length?
            <div className="event-grid">{page.items.map(event=><EventCard key={event.id} event={event}/>)}</div>:
            <div className="empty-state">No matches with the selected conditions.</div>
          }
          {page.next_cursor && activeRequest &&
            <div className="pagination">
              <button type="button" disabled={busy} onClick={()=>void execute({...activeRequest,cursor:page.next_cursor},true)}>
                {busy?'Loading…':'Load more'}
              </button>
            </div>
          }
        </>
      }
    </section>
  </div>;
}
