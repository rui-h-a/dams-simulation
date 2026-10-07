// Serialized-slot research protocol. No network client or deployed BFT claim.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const crypto = require('node:crypto');
const {Wallet,TypedDataEncoder,verifyTypedData,ZeroAddress}=require('ethers');
const domain={name:'DAMS research record',version:'1',chainId:31337,verifyingContract:ZeroAddress};
const types={Record:[{name:'epoch',type:'uint256'},{name:'sequence',type:'uint256'},
  {name:'scope',type:'string'},{name:'event',type:'string'},{name:'previous',type:'bytes32'},
  {name:'evidence',type:'bytes32'}]};
const hash=x=>TypedDataEncoder.hash(domain,types,x);
const digest=x=>'0x'+crypto.createHash('sha256').update(x).digest('hex');
const ZERO='0x'+'00'.repeat(32);
class Signer {
 constructor(wallet){this.wallet=wallet;this.locks=new Map();this.online=true;}
 async sign(record,corrupt=false){
  if(!this.online)throw Error('offline');
  const slot=`${record.epoch}:${record.sequence}`,h=hash(record);
  if(!corrupt&&this.locks.has(slot)&&this.locks.get(slot)!==h)throw Error('equivocation refused');
  this.locks.set(slot,h);return this.wallet.signTypedData(domain,types,record);
 }
}
class Ledger {
 constructor(addresses,threshold,scope='guild:0'){
  assert(Array.isArray(addresses)&&addresses.length>0,'authorized identities required');
  assert(new Set(addresses).size===addresses.length,'duplicate configured identity');
  assert(Number.isInteger(threshold)&&threshold>=1&&threshold<=addresses.length,'invalid quorum threshold');
  assert(typeof scope==='string'&&scope.length>0,'scope required');
  this.addresses=new Set(addresses);this.threshold=threshold;this.scope=scope;
  this.epoch=0;this.sequence=0;this.head=ZERO;this.events=new Set();this.records=[];
 }
 accept(record,signatures){
  assert.equal(record.epoch,this.epoch,'retired epoch');
  assert.equal(record.sequence,this.sequence+1,'replay/sequence');
  assert.equal(record.scope,this.scope,'wrong scope');assert.equal(record.previous,this.head,'fork/previous head');
  assert(!this.events.has(record.event),'duplicate event');
  const addresses=new Set(signatures.map(s=>verifyTypedData(domain,types,record,s)));
  assert([...addresses].every(a=>this.addresses.has(a)),'unknown credential');
  assert(addresses.size>=this.threshold,'no live authorized quorum');
  this.sequence=record.sequence;this.head=hash(record);this.events.add(record.event);
  this.records.push({record,signatures});
 }
 state(){return {addresses:[...this.addresses],threshold:this.threshold,scope:this.scope,epoch:this.epoch,records:this.records};}
 static restore(state){
  const x=new Ledger(state.addresses,state.threshold,state.scope);x.epoch=state.epoch;
  for(const r of state.records)x.accept(r.record,r.signatures);return x;
 }
}
function proposal(ledger,event='work:0:0',evidence='observed-work'){
 return {epoch:ledger.epoch,sequence:ledger.sequence+1,scope:ledger.scope,event,
  previous:ledger.head,evidence:digest(evidence)};
}
async function sigs(record,signers,corrupt=false){return Promise.all(signers.map(x=>x.sign(record,corrupt)));}
(async()=>{
 const wallets=Array.from({length:4},()=>Wallet.createRandom());const members=wallets.map(w=>w.address);
 const signers=wallets.map(w=>new Signer(w));const tests=[];
 for(const t of [0,-1,5,1.5])assert.throws(()=>new Ledger(members,t));
 assert.throws(()=>new Ledger([],1));assert.throws(()=>new Ledger([members[0],members[0]],1));
 tests.push('invalid thresholds, empty sets and duplicate configured identities rejected');
 const record=proposal(new Ledger(members,3));
 let signatures=await sigs(record,signers.slice(0,3));
 const ledger=new Ledger(members,3);ledger.accept(record,signatures);tests.push('valid 3-of-4 typed confirmation');
 assert.throws(()=>ledger.accept(record,signatures));tests.push('replay and sequence rejected');
 const next=proposal(ledger,record.event);const ns=await sigs(next,signers.slice(0,3));
 assert.throws(()=>ledger.accept(next,ns));tests.push('duplicate stable event rejected');
 const wrong=proposal(new Ledger(members,3));wrong.scope='guild:1';
 const ws=await sigs(wrong,wallets.slice(0,3).map(w=>new Signer(w)));
 assert.throws(()=>new Ledger(members,3).accept(wrong,ws));tests.push('signed wrong scope rejected');
 assert.throws(()=>new Ledger(members,3).accept(record,[signatures[0],signatures[0],signatures[1]]));tests.push('duplicate credentials do not increase quorum');
 const outsider=new Signer(Wallet.createRandom());
 assert.throws(()=>new Ledger(members,3).accept(record,[signatures[0],signatures[1],/* replaced below */signatures[2]].map((s,i)=>i===2?'0x'+'00'.repeat(65):s)));
 const os=await outsider.sign(record);
 assert.throws(()=>new Ledger(members,3).accept(record,[signatures[0],signatures[1],os]));tests.push('invalid and outsider signatures rejected');
 const tampered={...record,evidence:digest('changed')};
 assert.throws(()=>new Ledger(members,3).accept(tampered,signatures));tests.push('changed evidence invalidates signatures');
 const conflict={...record,evidence:digest('conflicting-work')};
 await assert.rejects(()=>signers[0].sign(conflict));tests.push('honest in-memory slot lock refuses equivocation');
 const central=new Ledger([members[0]],1);central.accept(record,[signatures[0]]);
 // Authentication binds a statement; it does not establish the truth of work.
 const falseRecord=proposal(central,'false-source:1','invented-work');
 const admin=new Signer(wallets[0]);central.accept(falseRecord,await sigs(falseRecord,[admin]));tests.push('trusted administrator can confirm a false source: residual failure');
 const falseQuorumRecord=proposal(new Ledger(members,3),'false-source:quorum','invented-work');
 const blindSigners=wallets.map(w=>new Signer(w));
 new Ledger(members,3).accept(falseQuorumRecord,await sigs(falseQuorumRecord,blindSigners.slice(0,3)));
 tests.push('authenticated three-signer quorum without source appraisal can accept false work: residual failure');
 // With at most one equivocator, two distinct 3-of-4 quorums intersect in
 // at least two identities, so at least one honest signer would need to double-sign.
 const subsets=[[0,1,2],[0,1,3],[0,2,3],[1,2,3]];let checked=0;
 for(const a of subsets)for(const b of subsets)for(let corrupt=0;corrupt<4;corrupt++){
  const honestIntersection=a.filter(i=>b.includes(i)&&i!==corrupt);assert(honestIntersection.length>=1);checked++;
 }
 tests.push('64 exhaustive quorum-intersection checks under <=1 corrupt signer');
 const compromised=wallets.map(w=>new Signer(w));
 const sa=await sigs(record,[compromised[0],compromised[1],compromised[2]],true);
 const sb=await sigs(conflict,[compromised[0],compromised[1],compromised[3]],true);
 new Ledger(members,3).accept(record,sa);new Ledger(members,3).accept(conflict,sb);
 tests.push('two compromised/colluding credentials can form conflicting accepted quorums: residual failure');
 // Network reachability is an explicit test harness, not measured networking.
 assert.throws(()=>new Ledger(members,3).accept(record,signatures.slice(0,2)));
 const fourthSignature=await new Signer(wallets[3]).sign(record);
 assert.throws(()=>new Ledger(members,3).accept(record,[signatures[2],fourthSignature]));
 new Ledger(members,3).accept(record,signatures);tests.push('2+2 partition stops both sides; 3+1 permits a majority');
 signers.forEach(s=>s.online=false);await assert.rejects(()=>sigs(record,signers));
 signers.forEach(s=>s.online=true);const restored=Ledger.restore(JSON.parse(JSON.stringify(ledger.state())));
 assert.equal(restored.head,ledger.head);assert.equal(restored.sequence,ledger.sequence);
 tests.push('all-node outage blocks signing; persisted signed-record recovery retains head');
 const damaged=JSON.parse(JSON.stringify(ledger.state()));damaged.records[0].record.evidence=digest('disk-corruption');
 assert.throws(()=>Ledger.restore(damaged));tests.push('corrupt persisted evidence rejected at recovery');
 const migrated=new Ledger([members[0],members[1],members[2],outsider.wallet.address],3);migrated.epoch=1;
 assert.throws(()=>migrated.accept(record,signatures));tests.push('new explicitly configured epoch rejects old-record replay');
 const result={status:'passed',source_sha256:crypto.createHash('sha256').update(fs.readFileSync(__filename)).digest('hex'),
  ethers:require('ethers').version,tests,exhaustive_quorum_cases:checked,
  scope:'local serialized-slot typed-signature harness; no network protocol, independent-controller deployment or full BFT consensus',
  key_policy:'ephemeral randomly generated test credentials, private key values never output or stored',
  assumptions:'fixed authorized identities, intact honest slot locks, correct public-key configuration, threshold 3 of 4; two compromised keys violate the assumed fault bound',
  not_tested:['log-tail omission without an external trusted head checkpoint','cross-epoch history continuity','certificate aggregation against invalid-extra-signature denial of service','durable signer-lock recovery or rollback protection','authorized membership/key migration protocol','end-to-end confidentiality','real geographic networking or clock bounds','production consensus-client safety/liveness','truth of off-chain work','legal adjudication']};
 fs.writeFileSync('trust-protocol-result.json',JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify({status:result.status,groups:tests.length,quorum_cases:checked,scope:result.scope},null,2));
})().catch(e=>{console.error(e.message);process.exitCode=1;});
