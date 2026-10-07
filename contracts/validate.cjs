'use strict';
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const crypto = require('node:crypto');
const dep = n => require(process.env.DAMS_NODE_MODULES ? path.join(process.env.DAMS_NODE_MODULES, n) : n);
const solc = dep('solc');
const ganache = dep('ganache');
const { ethers } = dep('ethers');
const sourcePath = process.argv[2] || path.resolve(__dirname, 'DAMSGovernance.sol');
const outputPath = process.argv[3] || path.resolve(__dirname, 'validation-result.json');
const source = fs.readFileSync(sourcePath, 'utf8');
const input = {language:'Solidity', sources:{'DAMSGovernance.sol':{content:source}}, settings:{optimizer:{enabled:true,runs:200}, evmVersion:'shanghai',outputSelection:{'*':{'*':['abi','evm.bytecode.object']}}}};
const compiled = JSON.parse(solc.compile(JSON.stringify(input)));
const diagnostics = compiled.errors || [];
assert.equal(diagnostics.filter(x=>x.severity==='error').length,0,JSON.stringify(diagnostics));
const artifact = compiled.contracts['DAMSGovernance.sol'].DAMSGovernance;
// Independent binary-search oracle: no Babylonian update shared with Solidity.
function root(x) {let low=0n,high=1n<<128n;while(low+1n<high){let m=(low+high)/2n;if(m*m<=x)low=m;else high=m;}return low;}
(async()=>{
 const backend=ganache.provider({logging:{quiet:true},wallet:{totalAccounts:8},chain:{hardfork:'shanghai'}});
 const provider=new ethers.BrowserProvider(backend); provider.pollingInterval=10;
 const admin=await provider.getSigner(0), outsider=await provider.getSigner(7);
 const c=await new ethers.ContractFactory(artifact.abi,artifact.evm.bytecode.object,admin).deploy();await c.waitForDeployment();
 const A=ethers.id('guild-A'),B=ethers.id('guild-B'),empty=ethers.id('empty');
 const people=await Promise.all([1,2,3,4,5,6].map(async i=>(await provider.getSigner(i)).getAddress()));
 const labels=[]; let reference=0;
 async function check(name,fn){await fn();labels.push(name);}
 async function tx(p){await (await p).wait();}
 async function reject(p){await assert.rejects(p);}
 async function issue(p,g,amount){const nonce=await c.nextNonce(g,p),ref=ethers.id('ref-'+(++reference));await tx(c.attestContribution(p,g,amount,ref,nonce));return ref;}
 async function invariant(g){const members=await c.guildMembers(g);assert.equal(new Set(members.map(x=>x.toLowerCase())).size,members.length);let sum=0n;for(const p of members)sum+=root(await c.contribution(g,p));assert.equal(await c.authorityDenominator(g),sum);return sum;}
 await check('uint256 integer-root boundary and 64 deterministic independent-oracle cases',async()=>{
  const maximum=(1n<<256n)-1n;
  const values=[0n,1n,2n,3n,4n,8n,9n,15n,16n,17n,maximum];
  for(let i=0;i<64;i++) values.push(BigInt('0x'+crypto.createHash('sha256').update('root-case-'+i).digest('hex')));
  for(const x of values){const y=await c.integerSqrt(x);assert.equal(y,root(x));assert(y*y<=x);assert((y+1n)*(y+1n)>x);}
 });
 await check('outsider cannot issue, revoke, assign or exit',async()=>{
  const o=c.connect(outsider);await reject(o.assignGuild.staticCall(people[0],A));await reject(o.attestContribution.staticCall(people[0],A,1,ethers.id('a'),0));await reject(o.revokeContribution.staticCall(ethers.id('a')));await reject(o.removeFromGuild.staticCall(people[0]));
 });
 await check('invalid member/guild and canonical no-duplicate membership',async()=>{
  await reject(c.assignGuild.staticCall(ethers.ZeroAddress,A));await reject(c.assignGuild.staticCall(people[0],ethers.ZeroHash));
  for(const p of people)await tx(c.assignGuild(p,A));await reject(c.assignGuild.staticCall(people[0],A));await invariant(A);
 });
 await check('empty and all-zero guild have explicit no-authority policy',async()=>{
  await reject(c.authorityFraction(empty,people[0]));await reject(c.authorityFraction(A,people[0]));assert.equal(await c.authorityDenominator(A),0n);
 });
 await check('scope, zero amount/reference, nonce and replay rejected',async()=>{
  const p=people[0];await reject(c.attestContribution.staticCall(p,B,1,ethers.id('bad'),0));await reject(c.attestContribution.staticCall(p,A,0,ethers.id('bad'),0));await reject(c.attestContribution.staticCall(p,A,1,ethers.ZeroHash,0));await reject(c.attestContribution.staticCall(p,A,1,ethers.id('bad'),99));
  const ref=await issue(p,A,100n);await reject(c.attestContribution.staticCall(p,A,100,ref,1));await reject(c.attestContribution.staticCall(p,A,1,ethers.id('stale'),0));await invariant(A);
 });
 await check('denominator unaffected by caller omission or duplicate lists; exact normalized fractions',async()=>{
  await issue(people[1],A,25n);const d=await invariant(A);assert.equal(d,15n);
  for(const p of people){const [n,denom]=await c.authorityFraction(A,p);assert.equal(denom,d);assert.equal(n,root(await c.contribution(A,p)));}
  assert.deepEqual(artifact.abi.find(x=>x.name==='authorityDenominator').inputs.map(x=>x.type),['bytes32']);
 });
 await check('move, old-scope revocation, return and exit preserve canonical sums',async()=>{
  const p=people[2],r=await issue(p,A,81n);await tx(c.assignGuild(p,B));await invariant(A);await invariant(B);assert.equal(await c.authorityWeight(p),0n);
  await tx(c.revokeContribution(r));await reject(c.revokeContribution.staticCall(r));await reject(c.attestContribution.staticCall(p,B,1,r,0));await tx(c.assignGuild(p,A));await invariant(A);await invariant(B);assert.equal(await c.authorityWeight(p),0n);
  await tx(c.removeFromGuild(p));assert.equal(await c.authorityWeight(p),0n);await invariant(A);await reject(c.authorityFraction(A,p));await tx(c.assignGuild(p,A));
 });
 await check('deterministic mixed mutation sequence independently checks cache, scope and credit totals',async()=>{
  const active=[];
  for(let i=0;i<36;i++){
   const p=people[i%people.length];const g=await c.guild(p);
   if(i%7===0){await tx(c.assignGuild(p,g===A?B:A));}
   else if(i%5===0&&active.length){const r=active.shift();await tx(c.revokeContribution(r));}
   else{active.push(await issue(p,g,BigInt((i+3)*(i+3))));}
   await invariant(A);await invariant(B);
   let total=0n;for(const group of [A,B]){let subtotal=0n;for(const member of people)subtotal+=await c.contribution(group,member);assert.equal(await c.guildContribution(group),subtotal);total+=subtotal;}assert.equal(await c.totalContribution(),total);
  }
 });
 await check('trusted administrator can attest false work under distinct references (documented residual)',async()=>{
  const p=people[0],g=await c.guild(p),before=await c.contribution(g,p);await issue(p,g,123n);await issue(p,g,123n);assert.equal(await c.contribution(g,p),before+246n);await invariant(g);
 });
 await check('checked overflow reverts atomically',async()=>{
  const p=people[0],g=await c.guild(p),before=await c.contribution(g,p),total=await c.totalContribution();await reject(c.attestContribution.staticCall(p,g,(1n<<256n)-1n,ethers.id('overflow'),await c.nextNonce(g,p)));assert.equal(await c.contribution(g,p),before);assert.equal(await c.totalContribution(),total);await invariant(g);
 });
 const result={status:'passed',source_sha256:crypto.createHash('sha256').update(source).digest('hex'),compiler:solc.version(),ethers:dep('ethers').version,ganache:dep('ganache/package.json').version,optimizer:{enabled:true,runs:200},evm:'shanghai',deployment:'in-process Ganache only, no public network',tests:labels,warning_count:diagnostics.filter(x=>x.severity==='warning').length,bytecode_bytes:artifact.evm.bytecode.object.length/2,limitations:['single trusted admin','public contributions','address uniqueness is not human uniqueness','no source-truth verification','no MACI/ZK/MPC or consensus implementation','no production security audit']};
 fs.writeFileSync(outputPath,JSON.stringify(result,null,2)+'\n');console.log(JSON.stringify({status:result.status,test_groups:labels.length,compiler:result.compiler,source_sha256:result.source_sha256,output:outputPath}));await backend.disconnect();
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
