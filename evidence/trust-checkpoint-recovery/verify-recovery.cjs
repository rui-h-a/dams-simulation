#!/usr/bin/env node
// Recheck a recovery candidate against the unchanged local record harness.
const assert = require('node:assert/strict');
const { createHash } = require('node:crypto');
const fs = require('node:fs');
const { createRequire } = require('node:module');
const path = require('node:path');
const vm = require('node:vm');

const SOURCE_COMMIT = '968ecadacedd88ad815a5f19de51de2011e853d0';
const PINS = {
  'trust-protocol-test.cjs': [9489, '56327de92c7b8bec313db46e535464d045818d6b66780b70ffae113d1c84061b'],
  'package.json': [287, 'ffc4375c6ab74326fddc0700ec9472c065fcdd84475c06283e7f07ac7f416ad6'],
  'package-lock.json': [31810, '79e55c72b6a07414c6b2f43f205d3717da90b975432f866a8d8a8a4330d9dc15'],
};

function sha256(bytes) {
  return createHash('sha256').update(bytes).digest('hex');
}

function loadOriginal(contracts) {
  for (const [name, [size, digest]] of Object.entries(PINS)) {
    const input = fs.readFileSync(path.join(contracts, name));
    assert.equal(input.length, size, `Pinned byte count differs: ${name}`);
    assert.equal(sha256(input), digest, `Pinned SHA-256 differs: ${name}`);
  }
  const loadDependency = createRequire(path.join(contracts, 'package.json'));
  const ethers = loadDependency('ethers');
  assert.equal(ethers.version, '6.13.5');
  const filename = path.join(contracts, 'trust-protocol-test.cjs');
  const source = fs.readFileSync(filename, 'utf8');
  const entry = source.indexOf('\n(async()=>{');
  assert(entry > 0, 'Expected standalone test entry');
  // Evaluate the definitions. The original test entry writes its own report and is not invoked here.
  const original = vm.runInNewContext(
    `${source.slice(0, entry)}\n;({Ledger, proposal, domain, types})`,
    { require: loadDependency },
    { filename },
  );
  return { ...original, ethers };
}

function restoreAgainstCheckpoint(Ledger, snapshot, trusted) {
  // The caller authenticates this configuration and checkpoint outside the supplied snapshot.
  const { addresses, threshold, scope, epoch, sequence, head } = trusted;
  assert(Number.isSafeInteger(epoch) && epoch >= 0, 'Invalid trusted epoch');
  assert(Number.isSafeInteger(sequence) && sequence >= 0, 'Invalid trusted sequence');
  assert(/^0x[0-9a-f]{64}$/.test(head), 'Invalid trusted head');
  assert(Array.isArray(snapshot.addresses), 'Snapshot roster is not an array');
  assert.deepEqual([...snapshot.addresses].sort(), [...addresses].sort(), 'Snapshot roster differs');
  assert.equal(snapshot.threshold, threshold, 'Snapshot threshold differs');
  assert.equal(snapshot.scope, scope, 'Snapshot scope differs');
  assert.equal(snapshot.epoch, epoch, 'Snapshot epoch differs');
  assert(Array.isArray(snapshot.records), 'Snapshot history is not an array');
  assert.equal(snapshot.records.length, sequence, 'Snapshot length differs from trusted sequence');

  const ledger = new Ledger(addresses, threshold, scope);
  ledger.epoch = epoch;
  for (const { record, signatures } of structuredClone(snapshot.records)) {
    ledger.accept(record, signatures);
  }
  assert.equal(ledger.sequence, sequence, 'Replayed sequence differs from trusted sequence');
  assert.equal(ledger.head, head, 'Replayed head differs from trusted head');
  return ledger;
}

function copySnapshot(ledger) {
  return JSON.parse(JSON.stringify(ledger.state()));
}

async function check(original) {
  const { Ledger, proposal, domain, types, ethers } = original;
  // Deterministic, disposable fixture keys. No production key is loaded or emitted.
  const wallets = Array.from({ length: 5 }, (_, index) => (
    new ethers.Wallet(`0x${sha256(`DAMS offline recovery fixture ${index}`)}`)
  ));
  const members = wallets.slice(0, 4);
  const roster = Object.freeze(members.map(wallet => wallet.address));
  const scope = 'guild:recovery';
  const epoch = 0;
  const threshold = 3;

  async function makeHistory({ members: signers = members, quorum = threshold,
    recordScope = scope, recordEpoch = epoch, label = 'legitimate' } = {}) {
    const ledger = new Ledger(signers.map(wallet => wallet.address), quorum, recordScope);
    ledger.epoch = recordEpoch;
    for (const sequence of [1, 2]) {
      const record = proposal(ledger, `record:${sequence}`, `${label} evidence ${sequence}`);
      // This fixture can sign alternate archives so recovery must enforce the external anchor.
      const signatures = await Promise.all(signers.slice(0, quorum).map(wallet => (
        wallet.signTypedData(domain, types, record)
      )));
      ledger.accept(record, signatures);
    }
    return ledger;
  }

  const originalLedger = await makeHistory();
  const checkpoint = Object.freeze({
    addresses: roster, threshold, scope, epoch, sequence: originalLedger.sequence, head: originalLedger.head,
  });
  const valid = copySnapshot(originalLedger);
  const recovered = restoreAgainstCheckpoint(Ledger, valid, checkpoint);
  assert.equal(recovered.head, Ledger.restore(valid).head);
  assert.equal(recovered.sequence, originalLedger.sequence);
  const cases = [{ case: 'legitimate_two_record_history', original: 'accepted', anchored: 'accepted' }];

  function rejectVariant(name, snapshot, error) {
    assert.equal(Ledger.restore(snapshot).sequence, snapshot.records.length);
    assert.throws(() => restoreAgainstCheckpoint(Ledger, snapshot, checkpoint), error);
    cases.push({ case: name, original: 'accepted', anchored: 'rejected' });
  }

  const substituted = await makeHistory({ members: [wallets[4]], quorum: 1 });
  rejectVariant('snapshot_supplies_substituted_roster', copySnapshot(substituted), /Snapshot roster differs/);
  const lowered = await makeHistory({ quorum: 1 });
  rejectVariant('snapshot_lowers_threshold', copySnapshot(lowered), /Snapshot threshold differs/);
  rejectVariant('valid_prefix_truncation', { ...valid, records: valid.records.slice(0, 1) }, /Snapshot length differs/);

  const tampered = copySnapshot(originalLedger);
  tampered.records[0].record.evidence = `0x${sha256('unsigned alteration')}`;
  assert.throws(() => Ledger.restore(tampered), /unknown credential/);
  assert.throws(() => restoreAgainstCheckpoint(Ledger, tampered, checkpoint), /unknown credential/);
  cases.push({ case: 'unsigned_history_alteration', original: 'rejected', anchored: 'rejected' });

  const alternate = await makeHistory({ label: 'alternate' });
  rejectVariant('fully_signed_alternate_at_same_height', copySnapshot(alternate), /Replayed head differs/);
  const wrongScope = await makeHistory({ recordScope: 'guild:other' });
  rejectVariant('signed_wrong_scope_history', copySnapshot(wrongScope), /Snapshot scope differs/);
  const wrongEpoch = await makeHistory({ recordEpoch: 1 });
  rejectVariant('signed_wrong_epoch_history', copySnapshot(wrongEpoch), /Snapshot epoch differs/);

  const fresh = restoreAgainstCheckpoint(Ledger, valid, checkpoint);
  assert.notEqual(fresh, recovered);
  assert.equal(fresh.head, recovered.head);
  assert.equal(Ledger.restore(valid).head, fresh.head);
  cases.push({ case: 'fresh_instance_with_same_configuration_and_checkpoint', original: 'accepted', anchored: 'accepted' });

  return {
    scope: 'Deterministic local recovery against an authenticated external configuration and exact checkpoint',
    source_commit: SOURCE_COMMIT,
    source_harness_sha256: PINS['trust-protocol-test.cjs'][1],
    ethers: ethers.version,
    checkpoint: { scope, epoch, threshold, sequence: checkpoint.sequence, head: checkpoint.head },
    case_count: cases.length,
    previously_accepted_variants_now_rejected: cases.filter(row => (
      row.original === 'accepted' && row.anchored === 'rejected'
    )).length,
    cases,
  };
}

async function main() {
  const [contracts, mode, ...extra] = process.argv.slice(2);
  assert(contracts && extra.length === 0 && (mode === undefined || mode === '--print-results'),
    'Usage: node verify-recovery.cjs PINNED_CONTRACTS_DIRECTORY [--print-results]');
  const result = await check(loadOriginal(path.resolve(contracts)));
  if (mode !== '--print-results') {
    const expected = JSON.parse(fs.readFileSync(path.join(__dirname, 'recovery-results.json'), 'utf8'));
    assert.deepEqual(result, expected, 'Recomputed case results differ from the retained report');
  }
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
}

main().catch(error => {
  console.error(error.message);
  process.exitCode = 1;
});
