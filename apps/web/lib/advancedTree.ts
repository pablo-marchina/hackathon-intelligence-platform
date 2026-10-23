/** Immutable, bounded editor state for the existing API's AND/OR/NOT AST. */
export type RuleNode = {
  kind:'rule'; id:number; field:string; op:string; value:string; negative:boolean;
};
export type GroupNode = {
  kind:'group'; id:number; join:'and'|'or'; negative:boolean; children:FilterNode[];
};
export type FilterNode = RuleNode | GroupNode;
export type Expression = Record<string,unknown>;

export const MAX_GROUP_CHILDREN=12;
export const MAX_VISUAL_GROUP_DEPTH=2;

export function newRule(id:number,field:string,op:string):RuleNode{
  return {kind:'rule',id,field,op,value:'',negative:false};
}

export function newRoot():GroupNode{
  return {kind:'group',id:0,join:'and',negative:false,children:[]};
}

export function modifyNode(
  root:GroupNode,id:number,update:(node:FilterNode)=>FilterNode,
):GroupNode{
  const visit=(node:FilterNode):FilterNode=>{
    if(node.id===id)return update(node);
    if(node.kind==='group')return {...node,children:node.children.map(visit)};
    return node;
  };
  return visit(root) as GroupNode;
}

export function appendNode(root:GroupNode,parentId:number,child:FilterNode):GroupNode{
  return modifyNode(root,parentId,node=>{
    if(node.kind!=='group'||node.children.length>=MAX_GROUP_CHILDREN)return node;
    return {...node,children:[...node.children,child]};
  });
}

export function removeNode(root:GroupNode,id:number):GroupNode{
  if(id===root.id)return root;
  const visit=(group:GroupNode):GroupNode=>({
    ...group,
    children:group.children.filter(child=>child.id!==id).map(child=>
      child.kind==='group'?visit(child):child,
    ),
  });
  return visit(root);
}

export function countEditorNodes(root:FilterNode):number{
  return 1+(root.kind==='group'
    ?root.children.reduce((sum,child)=>sum+countEditorNodes(child),0):0);
}

export function buildTreeExpression(
  root:GroupNode,
  resolveRule:(rule:RuleNode)=>Expression,
  maxDepth=5,
  maxNodes=32,
):Expression{
  const convert=(node:FilterNode):Expression=>{
    if(node.kind==='rule'){
      const result=resolveRule(node);
      return node.negative?{not:result}:result;
    }
    if(!node.children.length||node.children.length>MAX_GROUP_CHILDREN){
      throw new Error('Groups need between 1 and 12 conditions');
    }
    const children=node.children.map(convert);
    const group:Expression=children.length===1?children[0]:{[node.join]:children};
    return node.negative?{not:group}:group;
  };
  const expression=convert(root);
  let count=0;
  const check=(node:Expression,depth:number):void=>{
    count++;
    if(depth>maxDepth||count>maxNodes){
      throw new Error('Filter groups exceed the query complexity limit');
    }
    if('not' in node){
      check(node.not as Expression,depth+1);
    }else if('and' in node||'or' in node){
      const children=('and' in node?node.and:node.or) as Expression[];
      if(children.length<1||children.length>MAX_GROUP_CHILDREN)throw new Error('Too many conditions in a group');
      children.forEach(child=>check(child,depth+1));
    }
  };
  check(expression,0);
  return expression;
}
