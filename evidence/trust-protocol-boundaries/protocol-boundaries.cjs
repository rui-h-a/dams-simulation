// Local audit of the immutable public DAMS harness. No deployment or simulation.
// Usage: node protocol-boundaries.cjs PINNED_SOURCE_ROOT INSTALLED_DEPENDENCY_ROOT
// Roots contain contracts/package.json. Retrieve PINNED_SOURCE_ROOT from the commit below.
// The dependency root must already have the public contract dependencies installed.
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
const fs = require('node:fs')
const { createRequire } = require('node:module')
const path = require('node:path')
const vm = require('node:vm')

const commit = '968ecadacedd88ad815a5f19de51de2011e853d0'
const expectedHashes = {
  'contracts/trust-protocol-test.cjs': '56327de92c7b8bec313db46e535464d045818d6b66780b70ffae113d1c84061b',
  'contracts/trust-protocol-result.json': '2497407c323597f65ea8177ae6d2be9ee6000c559585b3533d4b34458e7f494e',
  'contracts/package.json': 'ffc4375c6ab74326fddc0700ec9472c065fcdd84475c06283e7f07ac7f416ad6',
  'contracts/package-lock.json': '79e55c72b6a07414c6b2f43f205d3717da90b975432f866a8d8a8a4330d9dc15',
  'contracts/DAMSGovernance.sol': '449835834708d5bd8a91f43d8ce29dce671ca0f603239bc6b87ae77d9163c16a',
  'contracts/validation-result.json': 'b1b39e691402766bbb77794a05d23d04edba0d2703a43dfc22782478eea2e1b5',
}
const sha = value => crypto.createHash('sha256').update(value).digest('hex')
const [, , sourceRoot, dependencyRoot] = process.argv
assert(sourceRoot && dependencyRoot, 'Supply the pinned source root and installed dependency root')
for (const [relative, digest] of Object.entries(expectedHashes)) {
  assert.equal(sha(fs.readFileSync(path.resolve(sourceRoot, relative))), digest, relative)
}

const harnessPath = path.resolve(sourceRoot, 'contracts/trust-protocol-test.cjs')
const source = fs.readFileSync(harnessPath, 'utf8')
const originalReceipt = JSON.parse(
  fs.readFileSync(path.resolve(sourceRoot, 'contracts/trust-protocol-result.json'), 'utf8')
)
const requireDependency = createRequire(path.resolve(dependencyRoot, 'contracts/package.json'))
const { Wallet, version: ethers } = requireDependency('ethers')
assert.equal(ethers, '6.13.5')

async function main() {
  let reproducedReceipt
  const readOnlyHarnessFS = {
    readFileSync(file) {
      assert.equal(file, harnessPath)
      return fs.readFileSync(file)
    },
    writeFileSync(file, content) {
      assert.equal(file, 'trust-protocol-result.json')
      reproducedReceipt = JSON.parse(content)
    },
  }
  const auditedRequire = module => module === 'node:fs'
    ? readOnlyHarnessFS
    : requireDependency(module)
  const processProxy = { exitCode: 0 }
  const silentConsole = {
    log() {},
    error(message) { throw new Error(message) },
  }
  // Run the unmodified official suite. Only its receipt write is intercepted.
  await vm.runInNewContext(source, {
    require: auditedRequire, __filename: harnessPath,
    process: processProxy, console: silentConsole,
  })
  assert.equal(processProxy.exitCode, 0)
  assert.deepEqual(reproducedReceipt, originalReceipt)

  // Expose the unchanged implementation without executing the test IIFE again.
  const entry = source.indexOf('(async()=>{')
  assert(entry > 0)
  const implementation = source.slice(0, entry) + '\n;({Signer, Ledger, proposal, sigs})'
  const { Signer, Ledger, proposal, sigs } = vm.runInNewContext(
    implementation, { require: auditedRequire }
  )
  const wallets = Array.from({ length: 4 }, () => Wallet.createRandom())
  const roster = wallets.map(wallet => wallet.address)
  const makeSigners = () => wallets.map(wallet => new Signer(wallet))
  const makeLedger = () => new Ledger(roster, 3, 'guild:0')
  const left = makeLedger()
  const right = makeLedger()
  const record = proposal(left)
  const signatures = await sigs(record, makeSigners().slice(0, 3))
  left.accept(record, signatures)
  assert.throws(() => left.accept(record, signatures), /replay\/sequence/)
  right.accept(record, signatures)
  assert.equal(left.head, right.head)
  assert.throws(() => new Ledger(roster, 3, 'guild:other').accept(record, signatures), /wrong scope/)

  const repeated = makeLedger()
  repeated.accept(record, [...signatures, signatures[0]])
  assert.equal(repeated.sequence, 1)
  assert.throws(
    () => makeLedger().accept(record, [signatures[0], signatures[1], signatures[0]]),
    /no live authorized quorum/
  )

  const attacker = Wallet.createRandom()
  const alternative = new Ledger([attacker.address], 1, 'guild:0')
  const fake = proposal(alternative, 'substituted:record', 'invented')
  const attackerSignature = await new Signer(attacker).sign(fake)
  alternative.accept(fake, [attackerSignature])
  assert.equal(Ledger.restore(JSON.parse(JSON.stringify(alternative.state()))).head, alternative.head)
  assert.notEqual(alternative.head, left.head)
  assert.throws(() => makeLedger().accept(fake, [attackerSignature]), /unknown credential/)

  const lowered = new Ledger(roster, 1, 'guild:0')
  const oneSignature = await new Signer(wallets[0]).sign(fake)
  lowered.accept(fake, [oneSignature])
  assert.equal(Ledger.restore(JSON.parse(JSON.stringify(lowered.state()))).head, lowered.head)
  assert.throws(() => makeLedger().accept(fake, [oneSignature]), /no live authorized quorum/)

  const splitLedger = makeLedger()
  const a = proposal(splitLedger, 'proposal:A', 'first')
  const b = proposal(splitLedger, 'proposal:B', 'second')
  const honest = makeSigners()
  const aVotes = await sigs(a, honest.slice(0, 2))
  const bVotes = await sigs(b, honest.slice(2))
  assert.throws(() => splitLedger.accept(a, aVotes), /no live authorized quorum/)
  assert.throws(() => splitLedger.accept(b, bVotes), /no live authorized quorum/)
  assert(honest.every(signer => signer.online))
  for (const signer of honest.slice(0, 2)) {
    await assert.rejects(() => signer.sign(b), /equivocation refused/)
  }
  for (const signer of honest.slice(2)) {
    await assert.rejects(() => signer.sign(a), /equivocation refused/)
  }
  assert.equal(splitLedger.sequence, 0)
  const unanimous = makeLedger()
  unanimous.accept(a, await sigs(a, makeSigners().slice(0, 3)))
  assert.equal(unanimous.sequence, 1)

  const scopeA = makeLedger()
  const scopeB = new Ledger(roster, 3, 'guild:other')
  const inScopeA = proposal(scopeA)
  const inScopeB = proposal(scopeB)
  const sharedSigners = makeSigners().slice(0, 3)
  scopeA.accept(inScopeA, await sigs(inScopeA, sharedSigners))
  for (const signer of sharedSigners) {
    await assert.rejects(() => signer.sign(inScopeB), /equivocation refused/)
  }
  assert.equal(scopeB.sequence, 0)
  scopeB.accept(inScopeB, await sigs(inScopeB, makeSigners().slice(0, 3)))
  assert.equal(scopeB.sequence, 1)

  const beforeRestart = makeLedger()
  const afterRestart = makeLedger()
  const first = proposal(beforeRestart, 'fork:first')
  const second = proposal(afterRestart, 'fork:second')
  const restartSigners = makeSigners()
  const [equivocator, honestRestart, honestLeft, honestRight] = restartSigners
  beforeRestart.accept(first, await Promise.all([
    equivocator.sign(first, true),
    honestRestart.sign(first),
    honestLeft.sign(first),
  ]))
  await assert.rejects(() => honestRestart.sign(second), /equivocation refused/)
  const restoredCredential = new Signer(wallets[1])
  afterRestart.accept(second, await Promise.all([
    equivocator.sign(second, true),
    restoredCredential.sign(second),
    honestRight.sign(second),
  ]))
  assert.equal(beforeRestart.sequence, 1)
  assert.equal(afterRestart.sequence, 1)
  assert.notEqual(beforeRestart.head, afterRestart.head)

  const later = proposal(left, 'later:1', 'later evidence')
  left.accept(later, await sigs(later, makeSigners().slice(0, 3)))
  const truncated = JSON.parse(JSON.stringify(left.state()))
  truncated.records.pop()
  const restored = Ledger.restore(truncated)
  assert.equal(restored.sequence, 1)
  assert.notEqual(restored.head, left.head)

  const invalidExtra = '0x' + '00'.repeat(65)
  const aggregate = makeLedger()
  assert.throws(() => aggregate.accept(record, [...signatures, invalidExtra]))
  assert.equal(aggregate.sequence, 0)

  const receipt = {
    scope: 'Local negative cases against an exact public research harness. No deployment or organizational simulation.',
    public_commit: commit,
    sha256: expectedHashes,
    runtime: { node: process.version, ethers },
    official_control: {
      status: reproducedReceipt.status,
      groups: reproducedReceipt.tests.length,
      quorum_intersection_cases: reproducedReceipt.exhaustive_quorum_cases,
      matches_stored_receipt: true,
    },
    checks: {
      same_slot_replay_within_accepted_instance_rejected: true,
      same_namespace_record_accepted_by_second_fresh_instance: true,
      wrong_scope_still_rejected: true,
      duplicate_signatures_have_no_weight_but_are_tolerated_as_extras: true,
      whole_snapshot_roster_substitution_restored: true,
      threshold_downgrade_with_unchanged_roster_restored: true,
      both_restore_cases_rejected_under_trusted_original_configuration: true,
      honest_two_plus_two_slot_locks_block_both_proposals_with_all_signers_online: true,
      shared_signer_objects_block_distinct_scopes_at_same_epoch_and_sequence: true,
      one_equivocator_and_one_lost_honest_lock_allow_conflicting_heads: true,
      valid_prefix_truncation_restores_without_external_checkpoint: true,
      invalid_extra_signature_rejects_otherwise_sufficient_aggregate: true,
    },
  }
  console.log(JSON.stringify(receipt, null, 2))
}

main().catch(error => {
  console.error(error)
  process.exitCode = 1
})
