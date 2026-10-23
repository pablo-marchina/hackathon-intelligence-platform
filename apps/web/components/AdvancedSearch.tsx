'use client';

import {useEffect,useRef,useState} from 'react';
import {EventCard} from './EventCard';
import {browserApiBase} from '../lib/api';
import type {HackathonPage} from '../lib/types';
import {
  appendNode,buildTreeExpression,countEditorNodes,MAX_GROUP_CHILDREN,
  MAX_VISUAL_GROUP_DEPTH,modifyNode,newRoot,newRule,removeNode,
} from '../lib/advancedTree';
import type {GroupNode,RuleNode} from '../lib/advancedTree';

type FilterField={key:string;type:'text'|'country'|'boolean'|'number'|'datetime';operators:string[];description:string};
type Catalog={schema_version:string;fields:FilterField[];max_depth:number;max_nodes:number};
type Expression=Record<string,unknown>;
const labels:Record<string,string>={
  eq:'equals',ne:'does not equal',in:'one of',gt:'greater than',
  gte:'at least',lt:'less than',lte:'at most',between:'between',
  contains:'contains text',is_known:'has information',is_unknown:'is unknown',
};
const valueNotRequired=new Set(['is_known','is_unknown']);
const title=(value:string)=>value.replaceAll('_',' ').replaceAll('.',' · ').replace(/\b\w/g,c=>c.toUpperCase());

function converted(field:FilterField,op:string,raw:string):unknown{
  if(valueNotRequired.has(op))return undefined;
  if(!raw.trim())throw new Error(`Fill in the value for ${title(field.key)}`);
  const convert=(entry:string):string|number|boolean=>{
    const value=entry.trim();
    if(!value)throw new Error('Filter values cannot be blank');
    if(field.type==='boolean'){
      if(value!=='true'&&value!=='false')throw new Error('Choose true or false');
      return value==='true';
    }
    if(field.type==='number'){
      const number=Number(value);
      if(!Number.isFinite(number))throw new Error('Enter a valid numeric value');
      return number;
    }
    return field.type==='country'?value.toUpperCase():value;
  };
  if(op==='in'||op==='between'){
    const parts=raw.split(',').map(convert);
    if(op==='between'&&parts.length!==2)throw new Error('Enter two values separated by a comma');
    if(op==='in'&&(parts.length===0||parts.length>20))throw new Error('Enter 1 to 20 values');
    return parts;
  }
  return convert(raw);
}

function RuleEditor({
  rule,catalog,onUpdate,onRemove,
}:{
  rule:RuleNode;
  catalog:Catalog;
  onUpdate:(id:number,change:Partial<RuleNode>)=>void;
  onRemove:(id:number)=>void;
}){
  const field=catalog.fields.find(item=>item.key===rule.field);
  const requiresValue=!valueNotRequired.has(rule.op);
  return <fieldset className="advanced-rule">
    <legend>Condition</legend>
    <label>Attribute
      <select value={rule.field} onChange={e=>onUpdate(rule.id,{field:e.target.value})}>
        {catalog.fields.map(item=><option key={item.key} value={item.key}>{title(item.key)}</option>)}
      </select>
    </label>
    <label>Operator
      <select value={rule.op} onChange={e=>onUpdate(rule.id,{op:e.target.value,value:''})}>
        {field?.operators.map(op=><option key={op} value={op}>{labels[op]??op}</option>)}
      </select>
    </label>
    {requiresValue&&<label>Value
      {field?.type==='boolean'?
        <select value={rule.value} onChange={e=>onUpdate(rule.id,{value:e.target.value})}>
          <option value="">Choose…</option><option value="true">Yes</option><option value="false">No</option>
        </select>:
        <input
          type={field?.type==='number'&&rule.op!=='in'&&rule.op!=='between'?'number':'text'}
          inputMode={field?.type==='number'?'decimal':undefined}
          value={rule.value}
          placeholder={rule.op==='between'?'1000, 5000':rule.op==='in'?'ONLINE, HYBRID':field?.type==='datetime'?'2026-11-01T00:00:00-03:00':'Enter a value'}
          onChange={e=>onUpdate(rule.id,{value:e.target.value})}
        />
      }
    </label>}
    {field?.description&&<small className="muted">{field.description}</small>}
    <div className="advanced-rule-actions">
      <label><input type="checkbox" checked={rule.negative} onChange={e=>onUpdate(rule.id,{negative:e.target.checked})}/> NOT</label>
      <button type="button" className="secondary" onClick={()=>onRemove(rule.id)}>Remove</button>
    </div>
  </fieldset>;
}

type GroupEditorProps={
  group:GroupNode;depth:number;catalog:Catalog;nodeCount:number;
  onUpdateGroup:(id:number,change:Partial<Pick<GroupNode,'join'|'negative'>>)=>void;
  onUpdateRule:(id:number,change:Partial<RuleNode>)=>void;
  onAddRule:(id:number)=>void;onAddGroup:(id:number)=>void;
  onRemove:(id:number)=>void;
};
function GroupEditor(props:GroupEditorProps){
  const {group,depth,catalog,nodeCount,onUpdateGroup,onUpdateRule,onAddRule,onAddGroup,onRemove}=props;
  const isRoot=depth===0;
  const hasRoom=group.children.length<MAX_GROUP_CHILDREN&&nodeCount<catalog.max_nodes;
  const canNest=hasRoom&&depth<MAX_VISUAL_GROUP_DEPTH&&nodeCount+2<=catalog.max_nodes;
  return <fieldset className={isRoot?'advanced-root-group':'advanced-nested-group'}>
    <legend>{isRoot?'Search expression':`Nested group · level ${depth}`}</legend>
    <div className="advanced-group-toolbar">
      <label>Combine conditions in this group
        <select value={group.join} onChange={e=>onUpdateGroup(group.id,{join:e.target.value as 'and'|'or'})}>
          <option value="and">ALL must match (AND)</option>
          <option value="or">ANY may match (OR)</option>
        </select>
      </label>
      {!isRoot&&<label className="advanced-inline-toggle">
        <input type="checkbox" checked={group.negative} onChange={e=>onUpdateGroup(group.id,{negative:e.target.checked})}/>
        Negate entire group (NOT)
      </label>}
    </div>
    {group.children.map(child=>
      child.kind==='group'
        ?<GroupEditor key={child.id} {...props} group={child} depth={depth+1}/>
        :<RuleEditor key={child.id} rule={child} catalog={catalog} onUpdate={onUpdateRule} onRemove={onRemove}/>
    )}
    <div className="advanced-group-buttons">
      <button type="button" className="secondary" disabled={!hasRoom} onClick={()=>onAddRule(group.id)}>+ Condition</button>
      <button type="button" className="secondary" disabled={!canNest} onClick={()=>onAddGroup(group.id)}>+ Nested group</button>
      {!isRoot&&<button type="button" className="secondary" onClick={()=>onRemove(group.id)}>Remove group</button>}
    </div>
  </fieldset>;
}

export function AdvancedSearch(){
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [tree,setTree]=useState<GroupNode>(newRoot);
  const nextId=useRef(2);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [page,setPage]=useState<HackathonPage|null>(null);
  const [activeRequest,setActiveRequest]=useState<Record<string,unknown>|null>(null);

  useEffect(()=>{
    const controller=new AbortController();
    fetch(`${browserApiBase()}/v1/hackathons/filter-fields`,{signal:controller.signal})
      .then(async res=>{
        if(!res.ok)throw new Error('Filter definitions are not available');
        return res.json() as Promise<Catalog>;
      }).then(data=>{
        setCatalog(data);
        const field=data.fields.find(f=>f.key==='format')??data.fields[0];
        if(field)setTree(old=>old.children.length?old:{...old,children:[newRule(1,field.key,field.operators[0])]});
      }).catch(err=>{
        if(!controller.signal.aborted)setError(err instanceof Error?err.message:'Unable to load filter fields');
      });
    return ()=>controller.abort();
  },[]);

  const updateRule=(id:number,change:Partial<RuleNode>)=>setTree(old=>
    modifyNode(old,id,node=>{
      if(node.kind!=='rule')return node;
      if(change.field&&change.field!==node.field){
        const field=catalog?.fields.find(item=>item.key===change.field);
        return field?newRule(node.id,field.key,field.operators[0]):node;
      }
      return {...node,...change};
    })
  );
  const updateGroup=(id:number,change:Partial<Pick<GroupNode,'join'|'negative'>>)=>setTree(old=>
    modifyNode(old,id,node=>node.kind==='group'?{...node,...change}:node)
  );
  const addRule=(id:number)=>{
    const field=catalog?.fields[0];
    if(!field)return;
    const newId=nextId.current++;
    setTree(old=>countEditorNodes(old)>=Math.min(32,catalog?.max_nodes??32)
      ?old:appendNode(old,id,newRule(newId,field.key,field.operators[0])));
  };
  const addGroup=(id:number)=>{
    const field=catalog?.fields[0];
    if(!field)return;
    const groupId=nextId.current++,ruleId=nextId.current++;
    setTree(old=>countEditorNodes(old)+2>Math.min(32,catalog?.max_nodes??32)
      ?old:appendNode(old,id,{
        kind:'group',id:groupId,join:'or',negative:false,
        children:[newRule(ruleId,field.key,field.operators[0])],
      }));
  };
  const remove=(id:number)=>setTree(old=>removeNode(old,id));

  const buildRequest=():Record<string,unknown>=>{
    if(!catalog)throw new Error('Filter definitions are not available');
    const expression=buildTreeExpression(tree,rule=>{
      const field=catalog.fields.find(item=>item.key===rule.field);
      if(!field||!field.operators.includes(rule.op))throw new Error('Unsupported field or operator');
      const leaf:Expression={field:rule.field,op:rule.op};
      if(!valueNotRequired.has(rule.op))leaf.value=converted(field,rule.op,rule.value);
      return leaf;
    },catalog.max_depth,catalog.max_nodes);
    return {limit:30,sort:'deadline',expression};
  };

  async function execute(request:Record<string,unknown>,more=false){
    setBusy(true);setError('');
    try{
      const response=await fetch(`${browserApiBase()}/v1/hackathons/query`,{
        method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify(request),
      });
      if(!response.ok){
        if(response.status===422)throw new Error('Filter combination is not valid. Review values, grouping and timezones.');
        throw new Error(`Search is unavailable (HTTP ${response.status})`);
      }
      const data=await response.json() as HackathonPage;
      setPage(old=>more&&old?{...data,items:[...old.items,...data.items]}:data);
      setActiveRequest(request);
    }catch(err){setError(err instanceof Error?err.message:'Search failed');}
    finally{setBusy(false);}
  }
  const submit=()=>{
    try{
      const request=buildRequest();
      setPage(null);
      void execute(request);
    }catch(err){setError(err instanceof Error?err.message:'Invalid search');}
  };
  const reset=()=>{
    const field=catalog?.fields.find(f=>f.key==='format')??catalog?.fields[0];
    setTree({...newRoot(),children:field?[newRule(nextId.current++,field.key,field.operators[0])]:[]});
    setError('');setPage(null);setActiveRequest(null);
  };

  return <div className="advanced-search-layout">
    <section className="filter-panel advanced-builder" aria-label="Advanced hackathon filters">
      <h2>Build your search</h2>
      <p className="muted">Combine fields using nested AND, OR and NOT. Missing information is not false. Only approved searchable attributes are listed.</p>
      {catalog?<GroupEditor
        group={tree} depth={0} catalog={catalog} nodeCount={countEditorNodes(tree)}
        onUpdateGroup={updateGroup} onUpdateRule={updateRule}
        onAddRule={addRule} onAddGroup={addGroup} onRemove={remove}
      />:!error&&<p className="muted">Loading filter definitions…</p>}
      <p className="muted">Max {catalog?.max_nodes??32} expression nodes and depth {catalog?.max_depth??5}. Empty groups cannot be searched.</p>
      <div className="advanced-builder-actions">
        <button type="button" className="secondary" disabled={!catalog||busy} onClick={reset}>Reset</button>
        <button type="button" disabled={!catalog||busy} onClick={submit}>{busy?'Searching…':'Find hackathons'}</button>
      </div>
      {error&&<p role="alert" className="warning">{error}</p>}
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
          {page.next_cursor&&activeRequest&&
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
