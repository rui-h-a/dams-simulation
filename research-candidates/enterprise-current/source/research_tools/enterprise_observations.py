"""Reported aggregate parser and preregistered data-role access boundaries.

No estimation is performed here. A locally frozen split cannot prove historical
blindness: the exposure ledger and external stewardship remain necessary.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
import hashlib
import json
import re


ROLES = {'training', 'calibration', 'validation', 'holdout_candidate', 'diagnostic'}
PURPOSES = {'training': 'design', 'calibration': 'fit', 'validation': 'evaluate_frozen',
            'diagnostic': 'inspect', 'holdout_candidate': 'evaluate_locked'}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


@dataclass(frozen=True)
class DataLabel:
    key: str
    organization: str
    industry: str
    countries: tuple[str, ...]
    period: str
    role: str
    exposure: str

    def validate(self):
        if not all(type(v) is str and v for v in (self.key, self.organization, self.industry, self.period)):
            raise ValueError('split metadata needs nonempty identifiers')
        if date.fromisoformat(self.period).isoformat() != self.period:
            raise ValueError('period must be canonical ISO YYYY-MM-DD')
        if self.role not in ROLES or self.exposure not in ('already_exposed', 'not_accessed_in_this_component'):
            raise ValueError('invalid role or declared exposure')
        if self.role == 'holdout_candidate' and self.exposure != 'not_accessed_in_this_component':
            raise ValueError('already exposed target cannot be a holdout candidate')
        if not self.countries or any(type(c) is not str or not c for c in self.countries):
            raise ValueError('countries must be nonempty labels')
        return self


def validate_split(labels):
    labels = tuple(labels)
    if len({r.key for r in labels}) != len(labels):
        raise ValueError('duplicate record split key')
    identity = set()
    for r in labels:
        r.validate()
        ident = (r.organization, r.period)
        if ident in identity:
            raise ValueError('same organization-period leaks across split labels')
        identity.add(ident)
    development = [r for r in labels if r.role in ('training', 'calibration')]
    evaluation = [r for r in labels if r.role in ('validation', 'holdout_candidate')]
    for r in evaluation:
        earlier = [d for d in development if d.organization == r.organization]
        if earlier and r.period <= max(d.period for d in earlier):
            raise ValueError('temporal evaluation must follow development periods')
    # Holdout candidates here request cross-enterprise, cross-industry and
    # cross-country evaluation jointly; no accidental overlap is accepted.
    nonhold = [r for r in labels if r.role != 'holdout_candidate']
    for h in (r for r in labels if r.role == 'holdout_candidate'):
        if any(h.organization == d.organization or h.industry == d.industry
               or set(h.countries) & set(d.countries) for d in nonhold):
            raise ValueError('holdout candidate overlaps declared development/evaluation dimensions')
    return labels


def freeze_split(path, labels, *, evaluation_model_sha256=None):
    """Create once, before the importer can read target values; never overwrite."""
    rows = [asdict(r) for r in validate_split(labels)]
    if evaluation_model_sha256 is not None and not re.fullmatch(r'[0-9a-f]{64}', evaluation_model_sha256):
        raise ValueError('evaluation model hash must be SHA256')
    payload = {'schema': 'enterprise-data-split-v1', 'labels': rows,
               'frozen_utc': datetime.now(timezone.utc).isoformat(),
               'blindness_status': 'candidate labels only; external historical-exposure audit not completed',
               'target_values_included': False,
               'evaluation_model_sha256': evaluation_model_sha256}
    payload['plan_sha256'] = hashlib.sha256(canonical(payload)).hexdigest()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('xb') as f:
        f.write(canonical(payload)+b'\n')
    return payload


def load_split(path):
    payload = json.loads(Path(path).read_bytes())
    expected = payload.pop('plan_sha256')
    if hashlib.sha256(canonical(payload)).hexdigest() != expected:
        raise ValueError('frozen split hash mismatch')
    payload['plan_sha256'] = expected
    labels = [DataLabel(**{**r, 'countries': tuple(r['countries'])}) for r in payload['labels']]
    return payload, {r.key: r for r in validate_split(labels)}


class PlainText(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, text):
        self.parts.append(text)


def parse_employee_total(raw, expected_date):
    """Parse a reported Amazon 10-K sentence, rejecting absent/ambiguous values.

    This narrow schema is explicit; other issuers need their own audited parser.
    An approximately reported headcount is not an exact real population or FTE.
    """
    target = date.fromisoformat(expected_date)
    parser = PlainText()
    parser.feed(raw.decode('utf-8'))
    text = ' '.join(' '.join(parser.parts).split())
    pattern = r'As of ([A-Za-z]+)\s+(\d{1,2})\s*,?\s+(\d{4})\s*,?\s+we employed\s+(approximately\s+)?([\d,]+)\s+full-time and part-time employees'
    matches = []
    for m in re.finditer(pattern, text, re.IGNORECASE):
        observed_date = datetime.strptime(f'{m[1]} {m[2]} {m[3]}', '%B %d %Y').date()
        if observed_date == target:
            number = m[5]
            if not re.fullmatch(r'(?:\d{1,3}(?:,\d{3})+|\d+)', number):
                raise ValueError('malformed reported count')
            count = int(number.replace(',', ''))
            if count < 1:
                raise ValueError('employee total must be positive')
            matches.append((count, bool(m[4])))
    if len(matches) != 1:
        raise ValueError('exactly one matching dated headcount sentence required')
    count, approximate = matches[0]
    return {'metric': 'reported_full_time_and_part_time_employees', 'unit': 'persons',
            'date': target.isoformat(), 'value': count, 'approximately_reported': approximate,
            'excluded_population': 'independent contractors and temporary personnel',
            'evidence_class': 'observed_reported_aggregate', 'calibration_status': 'not_estimated',
            'source_sha256': hashlib.sha256(raw).hexdigest()}


def import_employee_total(raw_path, split_path, key, purpose, source_url, model_sha256=None):
    """Authorise from frozen metadata before opening target bytes.

    Holdout candidates are deliberately inaccessible in this formative component
    until an independent data steward certifies past exposure and a sealed model.
    This function cannot certify who read a public report elsewhere.
    """
    plan, labels = load_split(split_path)
    if key not in labels:
        raise ValueError('target absent from frozen split')
    label = labels[key]
    if PURPOSES[label.role] != purpose:
        raise ValueError('requested purpose conflicts with frozen data role')
    if label.role == 'holdout_candidate':
        raise ValueError('holdout access sealed: independent stewardship is not implemented')
    if label.role == 'validation' and (not re.fullmatch(r'[0-9a-f]{64}', model_sha256 or '')
                                      or plan.get('evaluation_model_sha256') != model_sha256):
        raise ValueError('validation needs the frozen model hash bound into its split plan before target access')
    if label.organization != 'Amazon':
        raise ValueError('this issuer-specific parser only supports Amazon; use a separately audited parser')
    report_date = label.period.replace('-', '')
    pattern = rf'https://www\.sec\.gov/Archives/edgar/data/1018724/[0-9]+/amzn-{report_date}\.htm'
    if not re.fullmatch(pattern, source_url):
        raise ValueError('source URL does not match the supported issuer and target report period')
    result = parse_employee_total(Path(raw_path).read_bytes(), label.period)
    return {**result, 'record_key': key, 'organization': label.organization,
            'split_role': label.role, 'declared_exposure': label.exposure,
            'split_plan_sha256': plan['plan_sha256'], 'source_url': source_url,
            'evaluation_model_sha256': model_sha256,
            'parser_schema': 'amazon-10k-human-capital-sentence-v1'}
