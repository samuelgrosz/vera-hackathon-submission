from dataclasses import dataclass

from engine.normalize import country_names, date_comparison


CLEAR = 'DIFFERENT_ENTITY'
MATCH = 'SAME_ENTITY'
REVIEW = 'REVIEW'
CLEAR_CODES = ('ENTITY_TYPE_MISMATCH', 'DECEASED', 'TEMPORAL_IMPOSSIBILITY',
               'DOB_MISMATCH', 'NAME_MISMATCH_VERIFIED', 'LOCATION_MISMATCH')
CODES = {
    CLEAR: frozenset(CLEAR_CODES),
    MATCH: frozenset(('NAME_DOB_LOCATION_MATCH', 'NAME_DOB_MATCH', 'NAME_LOCATION_MATCH')),
    REVIEW: frozenset(('AMBIGUOUS_CUSTOMER_RECORD', 'INSUFFICIENT_DATA')),
}


@dataclass(frozen=True)
class Finding:
    decision: str
    code: str
    sentences: tuple
    evidence: tuple


@dataclass(frozen=True)
class CandidateResult:
    finding: Finding
    alternatives: tuple = ()
    blockers: tuple = ()


def _dates_text(evidence):
    return ', '.join(map(str, evidence.values))


def _countries_text(codes):
    return ', '.join(sorted(country_names.get(code, code) for code in codes))


def _names_text(values):
    return ', '.join(' '.join(value.split()) for value in values)


def _ids(*sources):
    return tuple(sorted(set(item for group in sources for item in group if item)))


def _age(birth, event):
    return event.year - birth.year - ((event.month, event.day) < (birth.month, birth.day))


def evaluate(customer, hit, screened, names):
    # A finding includes the same evidence used to evaluate its predicate.
    findings = []
    company = customer.kind == 'company'
    person = customer.kind == 'person'
    same_kind = bool(customer.kind and customer.kind == hit.kind)
    left_names = customer.verified_names or customer.names
    database_sources = (customer.id,) + customer.merge_sources
    name_source = customer.verified_sources or database_sources
    pair = (customer.id, hit.id)

    def finding(decision, code, first, second, *sources):
        return Finding(decision, code, (first, second), _ids(pair, customer.link_ids, *sources))

    def cleared(code, first, second, *sources):
        findings.append(finding(CLEAR, code, first, second, *sources))

    if customer.kind and hit.kind and customer.kind != hit.kind:
        cleared('ENTITY_TYPE_MISMATCH', f'The customer record describes a {customer.kind}.',
                f'The listing describes a {hit.kind}.', database_sources)
    if (hit.kind == 'person' and screened.values and not screened.uncertain and hit.death.values
            and not hit.death.uncertain
            and max(day.bounds()[1] for day in hit.death.values) < min(day.bounds()[0] for day in screened.values)):
        cleared('DECEASED', f'The listing records a death date of {_dates_text(hit.death)}.',
                f'That date is before the customer was screened on {_dates_text(screened)}.',
                hit.death.source_ids, screened.source_ids)
    if same_kind and hit.first_report and not hit.report_uncertain and customer.dob.values and not customer.dob.uncertain:
        report = hit.first_report.bounds()[0]
        if person and all(_age(day.bounds()[0], report) < 16 for day in customer.dob.values):
            cleared('TEMPORAL_IMPOSSIBILITY',
                    f'The earliest qualifying report dates to {report.isoformat()}.',
                    f'The customer birth information {_dates_text(customer.dob)} places the customer under 16 at that date.',
                    database_sources, customer.dob.source_ids)
        if company and all(day.bounds()[0] > report for day in customer.dob.values):
            cleared('TEMPORAL_IMPOSSIBILITY',
                    f'The earliest qualifying report dates to {report.isoformat()}.',
                    f'The customer company was incorporated after that date, in {_dates_text(customer.dob)}.',
                    database_sources, customer.dob.source_ids)

    comparisons = tuple(tuple(date_comparison(left, right) for right in hit.dob.values)
                        for left in customer.dob.values)
    date_complete = bool(customer.dob.values and hit.dob.values and not customer.dob.uncertain and not hit.dob.uncertain)
    date_agrees = date_complete and all('agrees' in items for items in comparisons)
    some_agreement = any('agrees' in items for items in comparisons)
    if same_kind and date_complete and all(item == 'contradicts' for items in comparisons for item in items):
        label = 'incorporation date' if company else 'birth information'
        cleared('DOB_MISMATCH', f'The customer {label} is {_dates_text(customer.dob)}.',
                f'Every admissible date for the listed entity ({_dates_text(hit.dob)}) contradicts it under the date rule.',
                database_sources, customer.dob.source_ids, hit.dob.source_ids)

    hit_parts = tuple(names.parts(name, company) for name in hit.names)
    left_parts = tuple(names.parts(name, company) for name in left_names)
    eligible = tuple(parts for parts in left_parts if len(parts) >= (1 if company else 2))
    agreement = tuple(any(names.agree(left, right) for right in hit_parts) for left in eligible)
    name_agrees = bool(agreement and (all(agreement) and len(eligible) == len(left_parts)
                                     if customer.verified_names else any(agreement)))
    name_agrees = name_agrees and not customer.verified_uncertain
    if (same_kind and customer.verified_names and not customer.verified_uncertain and not hit.name_uncertain
            and any(hit_parts) and eligible and len(eligible) == len(left_names) and not any(agreement)):
        cleared('NAME_MISMATCH_VERIFIED',
                f'The approved identity record gives the legal name {_names_text(customer.verified_names)}.',
                f'All eligible listing names and aliases ({_names_text(hit.names)}) differ under the name rule.',
                database_sources, customer.verified_sources)

    left_geo, right_geo = customer.geography.sources, hit.geography.sources
    overlap = left_geo.keys() & right_geo.keys()
    geography_uncertain = bool(customer.geography.uncertain_sources or hit.geography.uncertain_sources)
    disjoint = bool(left_geo and right_geo and not overlap and not geography_uncertain)
    location_guard = (person and bool(any(agreement)) and
                      (some_agreement or customer.dob.uncertain or hit.dob.uncertain or customer.verified_uncertain))
    if same_kind and disjoint and not location_guard:
        cleared('LOCATION_MISMATCH', f'The customer is linked to {_countries_text(left_geo)}.',
                f'The listing is linked to {_countries_text(right_geo)}, with no country in common.',
                database_sources, customer.geography.evidence(left_geo), hit.geography.evidence(right_geo),
                name_source, customer.dob.source_ids if name_agrees and customer.dob.values else (),
                hit.dob.source_ids if name_agrees and hit.dob.values else ())
    if findings:
        return CandidateResult(findings[0], tuple(findings))

    matched_names = tuple(name for name, right in zip(hit.names, hit_parts)
                          if name_agrees and any(names.agree(left, right) for left in eligible))
    if same_kind and person and name_agrees and date_agrees and not disjoint:
        location = f' The countries in common are {_countries_text(overlap)}.' if overlap else ''
        code = 'NAME_DOB_LOCATION_MATCH' if overlap else 'NAME_DOB_MATCH'
        return CandidateResult(finding(MATCH, code,
                               f'The customer name ({_names_text(left_names)}) agrees with the listing name or alias ({_names_text(matched_names)}).',
                               f'Birth information overlaps ({_dates_text(customer.dob)} and {_dates_text(hit.dob)}).{location}',
                               database_sources, name_source, customer.dob.source_ids, hit.dob.source_ids,
                               customer.geography.evidence(overlap), hit.geography.evidence(overlap)))
    if same_kind and company and name_agrees and overlap and not (customer.dob.uncertain or hit.dob.uncertain):
        return CandidateResult(finding(MATCH, 'NAME_LOCATION_MATCH',
                               f'The customer company name ({_names_text(left_names)}) agrees with a listing name ({_names_text(matched_names)}).',
                               f'Both are linked to {_countries_text(overlap)}.',
                               database_sources, name_source, customer.geography.evidence(overlap), hit.geography.evidence(overlap)))

    blockers = []
    facts = []
    evidence = [database_sources]
    if disjoint and location_guard and some_agreement:
        facts.append(f'The names and birth information can agree, while the known countries differ ({_countries_text(left_geo)} and {_countries_text(right_geo)})')
        blockers.append(('The name and birth information agree but the countries differ',
                         'Check the customer’s prior residence or nationality against the listing'))
        evidence.extend((name_source, customer.dob.source_ids, hit.dob.source_ids,
                         customer.geography.evidence(left_geo), hit.geography.evidence(right_geo)))
    if overlap:
        facts.append(f'Both sides are linked to {_countries_text(overlap)}')
        evidence.extend((customer.geography.evidence(overlap), hit.geography.evidence(overlap)))
    if not person and not company or not hit.kind:
        blockers.append(('The entity type is unresolved', 'Check whether both records describe an individual or a company'))
    if hit.problems:
        blockers.extend((problem, 'Check the birth date in the original listing') for problem in hit.problems)
    if person and not date_agrees and not (disjoint and location_guard and some_agreement):
        if customer.dob.values:
            facts.append(f'The customer birth information is {_dates_text(customer.dob)}')
            evidence.append(customer.dob.source_ids)
        if hit.dob.values:
            facts.append(f'The admissible listing birth information is {_dates_text(hit.dob)}')
            evidence.append(hit.dob.source_ids)
        if customer.dob.uncertain or not customer.dob.values:
            blockers.append(('The customer birth date is unresolved', 'Check the approved identity record for the customer’s birth date'))
        if (hit.dob.uncertain or not hit.dob.values) and not hit.problems:
            blockers.append(('The listing birth date is unresolved', 'Check the birth date in the original listing'))
        if customer.dob.values and hit.dob.values and not hit.dob.uncertain and not customer.dob.uncertain:
            blockers.append(('The available birth dates neither settle the match nor rule it out',
                             'Check the exact birth date of the entity in the listing'))
    if not name_agrees:
        blockers.append(('The names do not establish a match under the name rule',
                         'Check the approved legal name and the listing’s aliases'))
    if company and not overlap:
        blockers.append(('A shared country is unestablished', 'Check the country in each company’s registration record'))
    if geography_uncertain and not overlap:
        blockers.append(('Some country information is unresolved', 'Check the countries in the source records and residence notes'))
    if hit.report_uncertain and hit.first_report:
        blockers.append(('The report chronology is incomplete', 'Check the earliest qualifying report in the original profile'))
    if not blockers:
        blockers.append(('The available facts leave this identity comparison unresolved', 'Check the original listing for another identifying fact'))
    summary = '. '.join(facts[:3]) + '.' if facts else 'The available records leave identity unresolved.'
    # Keep the graded reason within the two-to-four-sentence contract.
    outcome = finding(REVIEW, 'INSUFFICIENT_DATA', summary, blockers[0][0] + '.', *evidence)
    return CandidateResult(outcome, blockers=tuple(dict.fromkeys(blockers)))


@dataclass(frozen=True)
class Decision:
    finding: Finding
    customer_id: str | None
    candidates: tuple
    blockers: tuple = ()


def decide(customers, hit, screened, names, *, alert_id):
    if not customers:
        issue = ('The customer record is unresolved', 'Check the account holder, login or email linked to this alert')
        return Decision(Finding(REVIEW, 'INSUFFICIENT_DATA',
                                ('The alert could not be tied to a customer record.',
                                 'Resolve the customer identity before comparing it with the listing.'),
                                _ids((alert_id, hit.id))), None, (), (issue,))
    outcomes = []
    for customer in customers:
        outcome = evaluate(customer, hit, screened, names)
        if outcome.finding.decision == CLEAR:
            replay = evaluate(customer, hit, screened, names)
            if replay != outcome or not outcome.finding.evidence:
                raise ValueError('clearance rule failed the source evidence replay')
        outcomes.append((customer.id, outcome))
    if len(outcomes) == 1:
        customer_id, outcome = outcomes[0]
        return Decision(outcome.finding, customer_id, tuple(outcomes), outcome.blockers)

    verdicts = {result.finding.decision for _, result in outcomes}
    labels = {CLEAR: 'would clear', MATCH: 'would match', REVIEW: 'needs review'}
    combined_evidence = _ids(*(result.finding.evidence for _, result in outcomes))
    if len(verdicts) != 1:
        outcomes_text = ', '.join(f'{customer_id} {labels[result.finding.decision]}'
                                  for customer_id, result in outcomes)
        issue = ('Several plausible customer records lead to different outcomes',
                 'Check which customer owns the account or supplied the approved identity document')
        return Decision(Finding(REVIEW, 'AMBIGUOUS_CUSTOMER_RECORD',
                               ('More than one distinct customer fits the alert.',
                                f'The results differ by customer record ({outcomes_text}).'),
                                combined_evidence), 'AMBIGUOUS', tuple(outcomes), (issue,))

    primary = outcomes[0][1].finding
    if primary.decision == CLEAR:
        common = set.intersection(*(set(item.code for item in result.alternatives) for _, result in outcomes))
        if common:
            code = next(code for code in CLEAR_CODES if code in common)
            witnesses = [next(fact for fact in result.alternatives if fact.code == code) for _, result in outcomes]
        else:
            witnesses = [result.finding for _, result in outcomes]
        primary = witnesses[0]
        combined_evidence = _ids(*(item.evidence for item in witnesses))
        details = ', '.join(f'{customer_id} ({witness.code})' for (customer_id, _), witness in zip(outcomes, witnesses))
        sentences = ('Every plausible customer has a separate policy-supported contradiction.',
                     f'For {outcomes[0][0]}, {primary.sentences[0][0].lower() + primary.sentences[0][1:]}',
                     primary.sentences[1],
                     f'The candidate-specific grounds are {details}.')
    elif primary.decision == MATCH:
        sentences = ('Each plausible customer separately meets the match rule.',
                     'The alert alone leaves which customer record applies unresolved.')
    else:
        sentences = ('Several customer records fit and each independently needs review.',
                     'The evidence leaves the comparison open for every plausible customer.')
    blockers = tuple(dict.fromkeys(issue for _, result in outcomes for issue in result.blockers))
    return Decision(Finding(primary.decision, primary.code, sentences, combined_evidence),
                    'AMBIGUOUS', tuple(outcomes), blockers)
