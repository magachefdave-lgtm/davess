"""Behavioral fixtures for the pure server plan, independent of SQLite and clocks."""
import ast
import copy
import datetime
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
CASES = json.loads((ROOT / 'tests/fixtures/holiday_supplement_cases.json').read_text())
NS = {}
PRODUCT = ROOT / 'backend/main.py'
SOURCE = PRODUCT.read_text()
# Execute the actual bundled pure domain, including its stdlib imports, without
# starting FastAPI or touching the machine's shared database/configuration.
START = SOURCE[:SOURCE.index('# BEGIN HOLIDAY CALCULATION')].count('\n') + 1
END = SOURCE[:SOURCE.index('# END HOLIDAY CALCULATION')].count('\n') + 1
DOMAIN = ast.Module(body=[node for node in ast.parse(SOURCE).body if START < node.lineno < END],type_ignores=[])
exec(compile(DOMAIN, str(PRODUCT), 'exec'), NS)


def calculate(state, draft=None, today='2026-11-09'):
    return NS['hs_calculate'](state, draft or CASES['cases'][0]['draft'], datetime.date.fromisoformat(today))


def state_for(index=0):
    return copy.deepcopy(CASES['cases'][index]['state'])


def committed(state, plan):
    result = copy.deepcopy(state)
    result['schedules'] = copy.deepcopy(plan['_schedules'])
    result['customHolidays'] = copy.deepcopy(plan['_customHolidays'])
    result['holidaySupplements'] = {'schema': 1, 'events': copy.deepcopy(plan['_events']),
                                   'bases': copy.deepcopy(plan['_bases']), 'requiredContract': 1}
    result['dataVersion'] += 1
    return result


def add(state, key, name='추가 공휴일'):
    return calculate(state, {'holidayIntent':'SET_CUSTOM','date_key':key,'name':name,'is_major':False})


def remove(state, key):
    return calculate(state, {'holidayIntent':'DELETE_CUSTOM','date_key':key})


def inherited_state(retain_cancelled=False):
    """A published 2027 credit, with no ordinary 2028 male reservation/anchor."""
    state=state_for()
    for offset in range(366):
        key=(datetime.date(2028,1,1)+datetime.timedelta(days=offset)).isoformat()
        state['noDuty'][key]=True
    state['noDuty'].pop('2028-06-14')
    state['schedules']['2028-06-14']=[{'name':'F1','role':'평일(여)','isSubstitute':False,'zeroScore':False}]
    if retain_cancelled:
        state['schedules']['2029-03-01']=[{'name':'M1','role':'공휴일(남)'}]
    state=committed(state,calculate(state))
    if retain_cancelled:
        state=committed(state,remove(state,'2027-06-09'))
    return state


class HolidaySupplementCalculationTests(unittest.TestCase):
    def assertBlocked(self, plan, code):
        self.assertEqual(plan.get('effect'), 'BLOCKED', {key:plan.get(key) for key in ('effect','blockers','candidate')})
        self.assertIn(code, [item['code'] for item in plan['blockers']])
        self.assertFalse(plan['eligible'])

    def test_add_uses_real_annual_tail_and_preserves_every_other_slot(self):
        for case in CASES['cases']:
            with self.subTest(case=case['id']):
                state = copy.deepcopy(case['state'])
                untouched = copy.deepcopy(state)
                result = calculate(state, case['draft'])
                self.assertEqual(result.get('effect'), 'ADD')
                self.assertEqual(result['candidate']['name'], case['expected']['candidate'])
                self.assertEqual(result['rotationBasis']['ordinary_tail_position'], case['expected']['ordinary_tail_position'])
                self.assertEqual(result['rotationBasis']['sequence_snapshot'], case['expected']['sequence_snapshot'])
                self.assertEqual(state, untouched, 'Preview may not mutate its input.')
                self.assertEqual(result['affectedDates'], ['2027-06-09'])
                self.assertEqual(result['preservedDatesCount'], len(state['schedules']) - 1)
                for key, slots in state['schedules'].items():
                    if key != '2027-06-09': self.assertEqual(result['_schedules'][key], slots)
                event = result['_events'][-1]
                self.assertEqual(event['female_credit']['before'], case['expected']['female_before'])
                self.assertEqual(event['female_credit']['after'], case['expected']['female_after'])
                self.assertTrue(event['female_credit']['consumes'])
                self.assertEqual(event['original_female_slot'], state['schedules']['2027-06-09'][0])

    def test_classifier_parity_for_every_supported_calendar_date(self):
        self.assertTrue(callable(NS.get('hs_category')), 'Pure classifier is not implemented.')
        state = state_for()
        for row in CASES['classification']:
            self.assertEqual(NS['hs_category'](state, row['date_key'], 'M'), row['category'], row['date_key'])
            self.assertEqual(NS['hs_major'](NS['hs_holidays'](state), row['date_key']), row['major'], row['date_key'])
            self.assertEqual(NS['hs_major_main'](NS['hs_holidays'](state), row['date_key']), row['main'], row['date_key'])

    def test_reverse_date_addition_consumes_after_previous_allocation(self):
        state = state_for()
        first = add(state, '2027-06-09')
        self.assertEqual(first.get('effect'), 'ADD')
        state = committed(state, first)
        second = add(state, '2027-04-07')
        self.assertEqual(second['effect'], 'ADD')
        self.assertEqual(second['candidate']['name'], 'M3')
        self.assertEqual(second['_events'][-1]['selected_ordinal'], 1)
        self.assertEqual(second['_events'][0], first['_events'][0])
        self.assertEqual(second['_schedules']['2027-06-09'], first['_schedules']['2027-06-09'])

    def test_absence_skips_are_saved_as_distance_not_allocation_sequence(self):
        state = state_for()
        state['absences'] = [{'name':'M2','type':'교육','start':'2027-06-09','end':'2027-06-09'}]
        result = calculate(state)
        self.assertEqual(result.get('effect'), 'ADD')
        self.assertEqual(result['candidate']['name'], 'M3')
        self.assertEqual(result['_events'][-1]['selected_ordinal'], 1)
        self.assertEqual(result['_events'][-1]['end_ordinal'], 2)
        self.assertEqual(result['_events'][-1]['skipped_candidates'][0]['code'], 'APPROVED_ABSENCE')
        state['absences'] += [{'name':name,'type':'연차','start':'2027-06-09','end':'2027-06-09'} for name in ['M1','M3']]
        self.assertBlocked(calculate(state), 'NO_ELIGIBLE_CANDIDATE')

    def test_female_out_of_turn_substitute_and_zero_score_preserved(self):
        state = state_for()
        state['schedules']['2027-06-09'] = [{'name':'F3','role':'대체당직','isSubstitute':True,'zeroScore':True}]
        result = calculate(state)
        self.assertEqual(result.get('effect'), 'ADD')
        credit = result['_events'][-1]['female_credit']
        self.assertEqual((credit['before'],credit['after'],credit['consumes']), (1,1,False))
        female = next(w for w in result['after']['workers'] if w['name'] == 'F3')
        self.assertEqual(female, {'name':'F3','role':'공휴일(여)','isSubstitute':True,'zeroScore':True})

    def test_virtual_female_credit_survives_later_actual_row_removal(self):
        state = state_for()
        plan = calculate(state)
        self.assertEqual(plan.get('effect'), 'ADD')
        state = committed(state, plan)
        state['schedules']['2027-06-09'] = [w for w in state['schedules']['2027-06-09'] if w['name'].startswith('M')]
        self.assertEqual(NS['hs_rotation_indexes'](state, '2027-06-10')['weekday|F'], 2)
        self.assertEqual(NS['hs_rotation_indexes'](state, '2028-01-01')['holiday|M'], 2)
        self.assertEqual(len(state['schedules']['2027-06-09']), 1)

    def test_repeated_name_save_is_noop_and_renaming_never_consumes(self):
        state = state_for()
        plan = calculate(state)
        self.assertEqual(plan.get('effect'), 'ADD')
        state = committed(state, plan)
        self.assertEqual(calculate(state)['effect'], 'NOOP')
        renamed = add(state, '2027-06-09', '공휴일 이름 수정')
        self.assertEqual(renamed['effect'], 'LABEL_ONLY')
        self.assertEqual(renamed['_events'], state['holidaySupplements']['events'])
        self.assertEqual(renamed['_schedules'], state['schedules'])

    def test_weekend_labels_do_not_consume_a_supplement(self):
        state=state_for()
        result=add(state,'2027-06-12')
        self.assertEqual(result.get('effect'),'LABEL_ONLY')
        self.assertEqual(result['_schedules'],state['schedules'])
        self.assertEqual(result['_events'],[])

    def test_base_override_deletion_and_restore_use_effective_transition(self):
        state=state_for()
        state['customHolidays']['2027-05-03']={'name':'사용자 이름','isMajor':False,'isRemoved':False}
        self.assertEqual(remove(state,'2027-05-03').get('effect'),'LABEL_ONLY')
        state['customHolidays']['2027-05-03']['isRemoved']=True
        state['schedules']['2027-05-03']=[{'name':'F1','role':'평일(여)','isSubstitute':False,'zeroScore':False}]
        result=calculate(state,{'holidayIntent':'RESTORE_BASE','date_key':'2027-05-03'})
        self.assertEqual(result['effect'],'ADD')

    def test_major_substitute_link_and_renaming_near_major_block_are_blocked(self):
        state=state_for()
        self.assertBlocked(add(state,'2027-09-17','대체공휴일'),'MAJOR_BLOCK')
        state['customHolidays']['2027-09-17']={'name':'임시공휴일','isMajor':False}
        self.assertBlocked(add(state,'2027-09-17','대체공휴일'),'MAJOR_BLOCK')
        self.assertBlocked(add(state,'2027-02-09','이름 변경'),'MAJOR_BLOCK')

    def test_eligibility_blockers_are_specific_and_never_mutate(self):
        mutations=[('CLOSED_MONTH',lambda s:s['closedMonths'].append('2027-06')),
                   ('NO_DUTY',lambda s:s['noDuty'].update({'2027-06-09':'RULE21'})),
                   ('FIXED_ORDER_REQUIRED',lambda s:s['rules'].update({'algoType':'FAIR_WEIGHT'})),
                   ('INVALID_FEMALE_SLOT',lambda s:s['schedules'].update({'2027-06-09':[]})),
                   ('INVALID_FEMALE_SLOT',lambda s:s['schedules']['2027-06-09'].append({'name':'F1','role':'평일(여)'})),
                   ('INVALID_FEMALE_SLOT',lambda s:s['schedules'].update({'2027-06-09':[{'name':'ghost','role':'평일(여)'}]})),
                   ('FEMALE_ABSENT',lambda s:s['absences'].append({'name':'F2','type':'연차','start':'2027-06-09','end':'2027-06-09'})),
                   ('ANNUAL_RESERVATIONS_INCOMPLETE',lambda s:s['schedules'].pop('2027-12-27'))]
        for code, mutate in mutations:
            with self.subTest(code=code):
                state=state_for();mutate(state);before=copy.deepcopy(state)
                result=calculate(state);self.assertBlocked(result,code)
                self.assertEqual(state,before)
                self.assertEqual(result['_schedules'],state['schedules'])
                self.assertEqual(result['_customHolidays'],state['customHolidays'])
        self.assertBlocked(calculate(state_for(),today='2027-06-09'),'NOT_FUTURE_DATE')
        self.assertBlocked(add(state_for(),'2030-06-10'),'UNSUPPORTED_YEAR')
        self.assertBlocked(add(state_for(),'2027-02-30'),'INVALID_DATE')

    def test_unrelated_closed_month_does_not_block_reading_annual_reservations(self):
        state=state_for();state['closedMonths']=['2027-01']
        self.assertEqual(calculate(state).get('effect'),'ADD')

    def test_shared_pool_requires_weekend_reservations_too(self):
        state=state_for(4);state['schedules'].pop('2027-12-26')
        self.assertBlocked(calculate(state),'ANNUAL_RESERVATIONS_INCOMPLETE')

    def test_cancelled_middle_credit_is_retained_then_contiguous_tail_released(self):
        state=state_for(); first=calculate(state)
        self.assertEqual(first.get('effect'),'ADD')
        state=committed(state,first);state=committed(state,add(state,'2027-04-07'))
        removed=remove(state,'2027-06-09')
        self.assertEqual(removed['effect'],'REMOVE')
        self.assertEqual(removed['_events'][0]['credit_state'],'CONSUMED')
        self.assertEqual(removed['_events'][1]['selected_ordinal'],1)
        state=committed(state,removed);removed=remove(state,'2027-04-07')
        self.assertTrue(all(e['credit_state']=='RELEASED' for e in removed['_events']))
        self.assertEqual(removed['_schedules']['2027-06-09'],state_for()['schedules']['2027-06-09'])
        state=committed(state,removed)
        self.assertEqual(add(state,'2027-03-10')['candidate']['name'],'M2')

    def test_next_year_reservations_retain_cancelled_male_credit(self):
        state=state_for();state['schedules']['2028-03-01']=[{'name':'M2','role':'공휴일(남)'}]
        plan=calculate(state);self.assertEqual(plan.get('effect'),'ADD')
        state=committed(state,plan)
        removed=remove(state,'2027-06-09')
        self.assertEqual(removed['effect'],'REMOVE')
        self.assertEqual(removed['_events'][0]['credit_state'],'CONSUMED')
        state=committed(state,removed)
        self.assertEqual(NS['hs_rotation_indexes'](state,'2028-01-01')['holiday|M'],2)

    def test_remove_never_restores_changed_or_deleted_approved_workers(self):
        state=state_for(); plan=calculate(state)
        self.assertEqual(plan.get('effect'),'ADD')
        state=committed(state,plan)
        state['schedules']['2027-06-09'][0]['zeroScore']=not state['schedules']['2027-06-09'][0].get('zeroScore',False)
        self.assertBlocked(remove(state,'2027-06-09'),'SLOT_BINDING_CHANGED')
        state['schedules']['2027-06-09']=[]
        self.assertBlocked(remove(state,'2027-06-09'),'SLOT_BINDING_CHANGED')

    def test_sequence_and_same_year_reservation_changes_require_occurrence_rebase(self):
        state=state_for();plan=calculate(state)
        self.assertEqual(plan.get('effect'),'ADD')
        state=committed(state,plan)
        for mutate in [lambda s:s['rules']['fixedOrders'].update({'holidayM':['M2','M1','M3']}),
                       lambda s:s['schedules']['2027-12-27'][0].update({'name':'M2'})]:
            changed=copy.deepcopy(state);mutate(changed)
            self.assertBlocked(add(changed,'2027-04-07'),'BASIS_REBASE_REQUIRED')
            rec=calculate(changed,{'holidayIntent':'RECONCILE','year':2027,'pool_key':'holiday|M','reconcilePolicy':'PRESERVE_EXISTING'})
            self.assertBlocked(rec,'BASIS_REBASE_REQUIRED')

    def test_pending_swap_is_checked_when_target_is_swap_date(self):
        state=state_for();state['requests']=[{'id':7,'request_type':'SWAP','status':'PENDING','target_date':'2027-06-10','swap_date':'2027-06-09','requester':'F3','target_worker':'F2','reason':'private reason'}]
        result=calculate(state)
        self.assertBlocked(result,'PENDING_SWAP_CATEGORY_CONFLICT')
        self.assertTrue(any(item.get('requestId')==7 for item in result['impacts']))
        self.assertNotIn('private reason',json.dumps({k:v for k,v in result.items() if not k.startswith('_')}))

    def test_pending_leave_is_warning_and_approved_absence_is_not_rewritten(self):
        state=state_for();state['requests']=[{'id':8,'request_type':'ABSENCE','status':'PENDING','target_date':'2027-06-09','end_date':'2027-06-10','requester':'M2','absence_type':'연차'}]
        result=calculate(state)
        self.assertEqual(result.get('effect'),'ADD')
        self.assertEqual(result['candidate']['name'],'M2')
        self.assertTrue(any(item.get('requestId')==8 for item in result['impacts']))

    def test_pending_leave_envelope_uses_real_absence_end_and_zero_day_blocker(self):
        state=state_for();state['requests']=[{'id':9,'request_type':'ABSENCE','status':'PENDING','target_date':'2027-06-08','absence_end_date':'2027-06-10','requester_name':'M2','absence_type':'연차'}]
        result=calculate(state)
        self.assertTrue(any(item.get('requestId')==9 for item in result['impacts']))
        request=state['requests'][0];request.update(target_date='2027-06-09',absence_end_date='2027-06-09')
        self.assertBlocked(calculate(state),'PENDING_ABSENCE_ZERO_DAYS')

    def test_amendment_and_cancel_original_envelope_are_included_without_private_reasons(self):
        state=state_for();state['requests']=[{'id':10,'request_type':'ABSENCE','status':'PENDING','target_date':'2027-07-01','absence_end_date':'2027-07-02','requester_name':'F2','absence_type':'교육','amends_request_id':5,'amendment_original':json.dumps({'name':'F2','start':'2027-06-09','end':'2027-06-10','type':'교육','details':{'memo':'private original reason'}})}]
        result=calculate(state)
        self.assertTrue(any(item.get('requestId')==10 for item in result['impacts']))
        self.assertNotIn('private original reason',json.dumps({k:v for k,v in result.items() if not k.startswith('_')}))

    def test_future_year_requires_reconcile_and_keeps_existing_rows_unchanged(self):
        state=copy.deepcopy(CASES['cases'][0]['future_state'])
        first=calculate(state)
        self.assertEqual(first['effect'],'ADD')
        state=committed(state,first)
        self.assertBlocked(add(state,'2028-06-07'),'BASIS_RECONCILE_REQUIRED')
        basis=next(row for row in state['holidaySupplements']['bases'] if row['year']==2028)
        result=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':basis['revision'],'reconcilePolicy':'PRESERVE_EXISTING','anchor':{'position':basis['anchor_before_year']}})
        self.assertEqual(result['effect'],'RECONCILE')
        self.assertEqual(result['affectedDates'],[])
        self.assertEqual(result['_schedules'],state['schedules'])
        self.assertEqual(result['_events'],state['holidaySupplements']['events'])
        state=committed(state,result)
        self.assertEqual(add(state,'2028-06-07')['effect'],'ADD')
        result=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','anchor':{'position':99}})
        self.assertBlocked(result,'INVALID_ANCHOR')

    def test_later_year_supplement_survives_reverse_year_add_and_prior_only_reconcile(self):
        state=copy.deepcopy(CASES['cases'][0]['future_state'])
        future=add(state,'2028-06-07');self.assertEqual(future['effect'],'ADD')
        state=committed(state,future);first=calculate(state)
        self.assertEqual(first['effect'],'ADD')
        self.assertEqual(first['_events'][0],future['_events'][0])
        state=committed(state,first)
        self.assertBlocked(add(state,'2028-04-05'),'BASIS_RECONCILE_REQUIRED')
        result=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','reconcilePolicy':'PRESERVE_EXISTING'})
        self.assertEqual(result['effect'],'RECONCILE')
        self.assertEqual(result['_events'],state['holidaySupplements']['events'])
        state=committed(state,result)
        self.assertEqual(add(state,'2028-04-05')['effect'],'ADD')

    def test_all_released_credits_allow_new_sequence_basis_without_remapping_old_events(self):
        state=state_for();state=committed(state,calculate(state));state=committed(state,remove(state,'2027-06-09'))
        state['rules']['fixedOrders']['holidayM']=['M3','M1','M2']
        result=add(state,'2027-04-07')
        self.assertEqual(result['effect'],'ADD')
        self.assertEqual(result['_events'][0],state['holidaySupplements']['events'][0])
        self.assertEqual(result['_events'][-1]['basis_revision'],2)
        self.assertEqual(result['_events'][-1]['allocation_seq'],2)

    def test_historical_replay_matches_regular_substitute_retired_and_previous_year_rules(self):
        state=state_for();state['schedules']={'2026-12-25':[{'name':'M2','role':'공휴일(남)'}]}
        self.assertEqual(NS['hs_rotation_indexes'](state,'2027-01-01')['holiday|M'],2)
        state['schedules']['2027-01-01']=[{'name':'M1','role':'공휴일(남)'}]
        state['schedules']['2027-03-01']=[{'name':'M3','role':'대체당직','isSubstitute':True,'zeroScore':True}]
        self.assertEqual(NS['hs_rotation_indexes'](state,'2027-05-01')['holiday|M'],1)
        state['schedules']['2027-03-01'][0]['name']='M2'
        self.assertEqual(NS['hs_rotation_indexes'](state,'2027-05-01')['holiday|M'],2)
        state['schedules']['2027-03-01']=[{'name':'retired','role':'공휴일(남)'}]
        self.assertEqual(NS['hs_rotation_indexes'](state,'2027-05-01')['holiday|M'],2)

    def test_reconcile_noop_returns_no_mutation_and_empty_dates(self):
        state=state_for();state=committed(state,calculate(state))
        result=calculate(state,{'holidayIntent':'RECONCILE','year':2027,'pool_key':'holiday|M'})
        self.assertEqual(result['effect'],'NOOP')
        self.assertEqual(result['_bases'],state['holidaySupplements']['bases'])
        self.assertEqual(result['affectedDates'],[])

    def test_switching_dedicated_pool_to_shared_pool_cannot_drop_consumed_credit(self):
        state=state_for();state=committed(state,calculate(state))
        state['rules']['fixedOrders']['holidayM']=[]
        self.assertBlocked(add(state,'2027-04-07'),'BASIS_REBASE_REQUIRED')

    def test_weekday_female_order_change_cannot_remap_saved_credit(self):
        state=state_for();state=committed(state,calculate(state))
        state['rules']['fixedOrders']['weekday']=['F3','F1','F2']
        self.assertBlocked(add(state,'2027-04-07'),'BASIS_REBASE_REQUIRED')

    def test_adopted_anchor_applies_at_january_first_before_any_ordinary_rows(self):
        state=copy.deepcopy(CASES['cases'][0]['future_state'])
        state=committed(state,calculate(state))
        plan=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','anchor':{'position':0}})
        self.assertEqual(plan['effect'],'RECONCILE')
        state=committed(state,plan)
        self.assertEqual(NS['hs_rotation_indexes'](state,'2028-01-01')['holiday|M'],0)

    def test_reconcile_requires_fixed_order_without_changing_algorithm(self):
        state=state_for();state=committed(state,calculate(state));state['rules']['algoType']='FAIR_WEIGHT'
        result=calculate(state,{'holidayIntent':'RECONCILE','year':2027,'pool_key':'holiday|M'})
        self.assertBlocked(result,'FIXED_ORDER_REQUIRED')

    def test_january_a_december_b_reserves_c_then_a_even_for_reverse_dates(self):
        state=state_for()
        for row in CASES['classification']:
            key=row['date_key']
            if key.startswith('2027-') and row['category']=='holiday' and key not in ('2027-01-01','2027-12-27'):
                state['noDuty'][key]=True
        state['schedules']['2027-01-01'][0]['name']='M1'
        state['schedules']['2027-12-27'][0]['name']='M2'
        first=calculate(state);self.assertEqual(first['candidate']['name'],'M3')
        state=committed(state,first)
        second=add(state,'2027-04-07');self.assertEqual(second['candidate']['name'],'M1')
        state=committed(state,second)
        self.assertEqual(NS['hs_rotation_indexes'](state,'2028-01-01')['holiday|M'],1)

    def test_single_female_turn_consumes_even_when_modulo_cursor_does_not_change(self):
        state=state_for();state['rules']['fixedOrders']['weekday']=['F2']
        result=calculate(state);self.assertEqual(result['effect'],'ADD')
        credit=result['_events'][-1]['female_credit']
        self.assertEqual((credit['before'],credit['after'],credit['consumes']),(0,0,True))

    def test_skips_are_released_with_last_cancelled_credit_and_not_reused_early(self):
        state=state_for();state['absences']=[{'name':'M2','type':'교육','start':'2027-06-09','end':'2027-06-09'}]
        first=calculate(state);self.assertEqual(first['_events'][-1]['end_ordinal'],2)
        state=committed(state,first);second=add(state,'2027-04-07')
        self.assertEqual(second['candidate']['name'],'M1')
        self.assertEqual(second['_events'][-1]['selected_ordinal'],2)
        state=committed(state,second);state=committed(state,remove(state,'2027-04-07'))
        self.assertEqual(add(state,'2027-03-10')['candidate']['name'],'M1')
        state=committed(state,remove(state,'2027-06-09'))
        self.assertEqual(add(state,'2027-03-10')['candidate']['name'],'M2')

    def test_retired_regular_historical_male_remains_an_annual_reservation(self):
        state=state_for();state['schedules']['2027-01-01'][0]['name']='retired'
        plan=calculate(state,today='2027-03-02')
        self.assertEqual(plan['effect'],'ADD')
        self.assertEqual(plan['candidate']['name'],'M2')

    def test_inherited_reordered_consumed_basis_blocks_new_year_without_anchor(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        prior=next(b for b in state['holidaySupplements']['bases'] if b['year']==2027)
        self.assertEqual(NS['hs_basis_review'](state,prior)['review_code'],'BASIS_REBASE_REQUIRED')
        before=copy.deepcopy(state)
        plan=add(state,'2028-06-14')
        self.assertBlocked(plan,'BASIS_RECONCILE_REQUIRED')
        self.assertEqual(plan['rotationBasis']['year'],2028)
        self.assertEqual(plan['rotationBasis']['revision'],0)
        self.assertEqual(plan['rotationBasis']['sequence_snapshot'],['M1','M3','M2'])
        self.assertEqual(plan['rotationBasis']['inherited_sources'][0]['year'],2027)
        self.assertEqual(plan['_bases'],state['holidaySupplements']['bases'])
        self.assertEqual(plan['_events'],state['holidaySupplements']['events'])
        self.assertEqual(plan['_schedules'],state['schedules'])
        self.assertEqual(state,before)

    def test_inherited_cancelled_consumed_basis_also_blocks_new_year(self):
        state=inherited_state(retain_cancelled=True)
        self.assertEqual((state['holidaySupplements']['events'][0]['state'],state['holidaySupplements']['events'][0]['credit_state']),('CANCELLED','CONSUMED'))
        state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        self.assertBlocked(add(state,'2028-06-14'),'BASIS_RECONCILE_REQUIRED')

    def test_inherited_pool_switch_and_ordinary_reservation_drift_are_not_silent(self):
        for mutation in ['pool','reservation']:
            with self.subTest(mutation=mutation):
                state=inherited_state()
                if mutation=='pool':
                    state['rules']['fixedOrders']['holidayM']=[]
                else:
                    state['schedules']['2027-12-27'][0]['name']='M3'
                plan=add(state,'2028-06-14')
                self.assertBlocked(plan,'BASIS_RECONCILE_REQUIRED')
                self.assertEqual(plan['rotationBasis']['pool_key'],'weekend|M' if mutation=='pool' else 'holiday|M')

    def test_inherited_tail_uses_saved_sequence_length_not_current_sequence(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1']
        indexes={'holiday|M':0}
        NS['hs_apply_year_tail'](state,indexes,2027)
        self.assertEqual(indexes['holiday|M'],2,'Saved tail 1 plus consumed ordinal 1 remains position 2 of saved 3-person sequence.')

    def test_inherited_fresh_reconcile_requires_revision_zero_and_explicit_valid_anchor(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        draft={'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':0,'reconcilePolicy':'PRESERVE_EXISTING'}
        self.assertBlocked(calculate(state,draft),'ANCHOR_REQUIRED')
        self.assertBlocked(calculate(state,{**draft,'anchor':{'position':3}}),'INVALID_ANCHOR')
        self.assertBlocked(calculate(state,{**draft,'expected_basis_revision':1,'anchor':{'position':1}}),'BASIS_REVISION_CHANGED')
        missing_revision={**draft,'anchor':{'position':1}};missing_revision.pop('expected_basis_revision')
        self.assertBlocked(calculate(state,missing_revision),'BASIS_REVISION_CHANGED')
        self.assertEqual(len(state['holidaySupplements']['bases']),1)

    def test_inherited_fresh_explicit_anchor_adopts_target_only_and_allows_add(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        prior_events=copy.deepcopy(state['holidaySupplements']['events']);prior_basis=copy.deepcopy(state['holidaySupplements']['bases'][0])
        plan=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':0,'anchor':{'position':1},'reconcilePolicy':'PRESERVE_EXISTING'})
        self.assertEqual(plan['effect'],'RECONCILE')
        self.assertEqual(plan['affectedDates'],[])
        self.assertEqual(plan['_schedules'],state['schedules'])
        self.assertEqual(plan['_events'],prior_events)
        self.assertEqual(plan['_bases'][0],prior_basis)
        self.assertEqual(plan['_bases'][-1]['anchor_before_year'],1)
        self.assertEqual(plan['_bases'][-1]['policy'],'PRESERVE_EXISTING')
        state=committed(state,plan)
        self.assertEqual(NS['hs_basis_review'](state,prior_basis)['review_code'],'BASIS_REBASE_REQUIRED')
        self.assertEqual(NS['hs_rotation_indexes'](state,'2028-01-01')['holiday|M'],1)
        added=add(state,'2028-06-14')
        self.assertEqual(added['effect'],'ADD')
        self.assertEqual(added['candidate']['name'],'M3')
        self.assertEqual(added['_events'][0],prior_events[0])

    def test_inherited_target_anchor_must_remain_current(self):
        state=copy.deepcopy(CASES['cases'][0]['future_state']);state=committed(state,calculate(state))
        state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        plan=calculate(state,{'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':1,'anchor':{'position':1},'reconcilePolicy':'PRESERVE_EXISTING'})
        self.assertEqual(plan['effect'],'RECONCILE')
        state=committed(state,plan)
        self.assertEqual(add(state,'2028-06-14')['effect'],'ADD')
        state['schedules']['2028-12-25'][0]['name']='M3'
        self.assertBlocked(add(state,'2028-06-14'),'BASIS_RECONCILE_REQUIRED')

    def test_inherited_prior_only_change_can_reconcile_without_rewriting_consumed_occurrences(self):
        state=inherited_state();state['schedules']['2026-12-25']=[{'name':'M2','role':'공휴일(남)'}]
        original_event=copy.deepcopy(state['holidaySupplements']['events'][0])
        self.assertBlocked(add(state,'2028-06-14'),'BASIS_RECONCILE_REQUIRED')
        source=calculate(state,{'holidayIntent':'RECONCILE','year':2027,'pool_key':'holiday|M','expected_basis_revision':1,'reconcilePolicy':'PRESERVE_EXISTING'})
        self.assertEqual(source['effect'],'RECONCILE')
        self.assertEqual(source['_events'][0],original_event)
        state=committed(state,source)
        self.assertEqual(add(state,'2028-06-14')['effect'],'ADD')

    def test_inherited_fresh_anchor_rejects_incomplete_year_and_wrong_pool_consumed_credit(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        draft={'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':0,'anchor':{'position':1},'reconcilePolicy':'PRESERVE_EXISTING'}
        state['noDuty'].pop('2028-03-01')
        self.assertBlocked(calculate(state,draft),'ANNUAL_RESERVATIONS_INCOMPLETE')
        state=copy.deepcopy(CASES['cases'][0]['future_state'])
        state=committed(state,add(state,'2028-06-14'))
        state['rules']['fixedOrders']['holidayM']=[]
        self.assertBlocked(calculate(state,{**draft,'pool_key':'weekend|M'}),'BASIS_REBASE_REQUIRED')

    def test_inherited_released_credit_does_not_require_a_new_year_anchor(self):
        state=inherited_state();state=committed(state,remove(state,'2027-06-09'))
        self.assertEqual(state['holidaySupplements']['events'][0]['credit_state'],'RELEASED')
        state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        self.assertEqual(add(state,'2028-06-14')['effect'],'ADD')

    def test_inherited_already_adopted_unchanged_anchor_reconcile_is_noop(self):
        state=inherited_state();state['rules']['fixedOrders']['holidayM']=['M1','M3','M2']
        draft={'holidayIntent':'RECONCILE','year':2028,'pool_key':'holiday|M','expected_basis_revision':0,'anchor':{'position':1},'reconcilePolicy':'PRESERVE_EXISTING'}
        state=committed(state,calculate(state,draft))
        plan=calculate(state,{**draft,'expected_basis_revision':1})
        self.assertEqual(plan['effect'],'NOOP')
        self.assertEqual(plan['_bases'],state['holidaySupplements']['bases'])

    def test_statistics_preview_honors_zero_score_rule_and_retains_worker_flag(self):
        for setting,expected_after in [(False,2),(True,0),(None,0)]:
            with self.subTest(substituteScoreZero=setting):
                state=state_for();state['schedules']['2027-06-09'][0]['zeroScore']=True
                if setting is None:
                    state['rules'].pop('substituteScoreZero',None)
                else:
                    state['rules']['substituteScoreZero']=setting
                plan=calculate(state)
                changes=next(impact['changes'] for impact in plan['impacts'] if impact['code']=='STATISTICS_CHANGE')
                female=next(change for change in changes if change['name']=='F2')
                self.assertEqual((female['beforePoints'],female['afterPoints']),(1,expected_after))
                self.assertTrue(next(worker for worker in plan['after']['workers'] if worker['name']=='F2')['zeroScore'])


if __name__=='__main__': unittest.main()
