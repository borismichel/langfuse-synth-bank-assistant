"""The gate consumes measured rows; missing evidence cannot approve a candidate."""
from synth.companion.experiment_gate import assess


def test_gate_separates_business_failures_missing_and_runner_errors():
    good={'case_id':'one','checks':{'language':1,'conduct':1,'dispute':1},'usage':{'input':100,'output':20},'verified':True}
    assert assess(['one'],[good],[good])['status']=='passed'
    bad={**good,'checks':{**good['checks'],'conduct':0}}
    report=assess(['one'],[good],[bad])
    assert report['status']=='failed' and report['failed']==1
    assert assess(['one'],[good],[])['status']=='incomplete'
    assert assess(['one'],[good],[{**good,'error':'provider_failed'}])['status']=='runner_failed'
    assert assess(['one'],[good],[{**good,'checks':{'language':1,'conduct':None,'dispute':1}}])['status']=='incomplete'
    assert assess(['one'],[good],[{**good,'verified':False}])['status']=='incomplete'
    cost={**good,'usage':{'input':300,'output':20}}
    assert assess(['one'],[good],[cost])['status']=='failed'
    assert assess(['one'],[good],[{**good,'usage':None}])['status']=='incomplete'
    assert assess(['one'],[good],[good,good])['status']=='runner_failed'


def test_uncertain_policy_and_empty_cohort_cannot_pass():
    from synth.companion.experiment_runner import verdict
    assert verdict('No violation.\npass',['pass','fail','unknown'])=='pass'
    assert verdict('probably pass',['pass','fail','unknown']) is None
    assert verdict('{"score":1}',['pass','fail','unknown']) is None
    assert verdict('unknown',['pass','fail','unknown'])=='unknown'
    assert assess([],[],[])['status']=='incomplete'
