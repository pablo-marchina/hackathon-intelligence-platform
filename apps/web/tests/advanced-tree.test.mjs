import test from 'node:test';
import assert from 'node:assert/strict';
import {
  appendNode,buildTreeExpression,countEditorNodes,modifyNode,newRoot,newRule,removeNode,
} from '../lib/advancedTree.ts';

const leaf=(rule)=>({field:rule.field,op:rule.op,value:rule.value});

test('nested AND/OR/NOT serialization matches the backend AST',()=>{
  let root=newRoot();
  root=appendNode(root,0,{...newRule(1,'format','eq'),value:'ONLINE'});
  root=appendNode(root,0,{
    kind:'group',id:2,join:'or',negative:false,
    children:[
      {...newRule(3,'attrs.language','eq'),value:'pt'},
      {...newRule(4,'eligible_country','eq'),value:'BR',negative:true},
    ],
  });
  assert.equal(countEditorNodes(root),5);
  const expression=buildTreeExpression(root,leaf);
  assert.deepEqual(expression,{
    and:[
      {field:'format',op:'eq',value:'ONLINE'},
      {or:[
        {field:'attrs.language',op:'eq',value:'pt'},
        {not:{field:'eligible_country',op:'eq',value:'BR'}},
      ]},
    ],
  });
  const negative=modifyNode(root,2,node=>({...node,negative:true}));
  assert.deepEqual(buildTreeExpression(negative,leaf).and[1],{
    not:{or:[
      {field:'attrs.language',op:'eq',value:'pt'},
      {not:{field:'eligible_country',op:'eq',value:'BR'}},
    ]},
  });
});

test('removing leaves/groups does not mutate old tree or root',()=>{
  let root=newRoot();
  root=appendNode(root,0,newRule(1,'format','eq'));
  root=appendNode(root,0,{
    kind:'group',id:2,join:'and',negative:false,children:[newRule(3,'is_free','eq')],
  });
  const changed=removeNode(root,3);
  assert.equal(root.children.length,2);
  assert.equal(changed.children.length,2);
  assert.equal(changed.children[1].children.length,0);
  assert.throws(()=>buildTreeExpression(changed,leaf),/Groups need/);
  const clean=removeNode(changed,2);
  assert.equal(clean.children.length,1);
  assert.equal(removeNode(clean,0),clean);
});

test('server limits are enforced locally including NOT group depth',()=>{
  let root=newRoot();
  root=appendNode(root,0,newRule(1,'format','eq'));
  assert.throws(()=>buildTreeExpression(root,leaf,5,0),/complexity/);
  for(let i=2;i<=14;i++)root=appendNode(root,0,newRule(i,'name','contains'));
  assert.equal(root.children.length,12);
  const rootWithNested=appendNode(newRoot(),0,{
    kind:'group',id:2,join:'or',negative:true,
    children:[{...newRule(3,'is_free','eq'),negative:true}],
  });
  assert.throws(()=>buildTreeExpression(rootWithNested,leaf,1,32),/complexity/);
});

test('single condition and invalid empty groups have honest semantics',()=>{
  const root=appendNode(newRoot(),0,newRule(1,'format','eq'));
  assert.deepEqual(buildTreeExpression(root,leaf),{
    field:'format',op:'eq',value:'',
  });
  assert.throws(()=>buildTreeExpression(newRoot(),leaf),/Groups need/);
});
