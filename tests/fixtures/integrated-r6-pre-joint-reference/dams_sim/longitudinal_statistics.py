"""Paired-world intervals; bounded closure endpoints do not use a Wald interval.

Continuous intervals use a Student-t approximation. Closure risk differences
use Bonferroni-combined exact binomial intervals for the two discordant outcomes;
the construction remains conservative without independence of the outcomes.
These intervals describe conditional Monte Carlo uncertainty, not uncertainty
about the uncalibrated model, parameter choices or real organizations.
"""
from __future__ import annotations
import math
import statistics


def _beta_fraction(a,b,x):
    # Continued fraction for the incomplete beta integral (double precision).
    qab=a+b;qap=a+1;qam=a-1;c=1.;d=1-qab*x/qap
    if abs(d)<1e-300:d=1e-300
    d=1/d;h=d
    for m in range(1,10001):
        m2=2*m;aa=m*(b-m)*x/((qam+m2)*(a+m2))
        d=1+aa*d
        if abs(d)<1e-300:d=1e-300
        c=1+aa/c
        if abs(c)<1e-300:c=1e-300
        d=1/d;h*=d*c;aa=-(a+m)*(qab+m)*x/((a+m2)*(qap+m2))
        d=1+aa*d
        if abs(d)<1e-300:d=1e-300
        c=1+aa/c
        if abs(c)<1e-300:c=1e-300
        d=1/d;delta=d*c;h*=delta
        if abs(delta-1)<3e-14:return h
    raise ArithmeticError('incomplete beta did not converge')


def regularized_beta(x,a,b):
    if not 0<=x<=1 or a<=0 or b<=0:raise ValueError('invalid beta argument')
    if x in (0.,1.):return x
    front=math.exp(math.lgamma(a+b)-math.lgamma(a)-math.lgamma(b)+a*math.log(x)+b*math.log1p(-x))
    if x<(a+1)/(a+b+2):return front*_beta_fraction(a,b,x)/a
    return 1-front*_beta_fraction(b,a,1-x)/b


def student_cdf(value,df):
    if type(df) is not int or df<1 or not math.isfinite(value):raise ValueError('invalid Student-t arguments')
    tail=.5*regularized_beta(df/(df+value*value),df/2,.5)
    return tail if value<0 else 1-tail


def student_quantile(probability,df):
    if not .5<probability<1:raise ValueError('upper Student-t probability required')
    low=0.;high=1.
    while student_cdf(high,df)<probability:
        high*=2
        if high>1e12:raise ArithmeticError('Student-t quantile is unbounded')
    for _ in range(80):
        mid=(low+high)/2
        if student_cdf(mid,df)<probability:low=mid
        else:high=mid
    return (low+high)/2


def _binomial_cdf(k,n,p):
    if k<0:return 0.
    if k>=n:return 1.
    if p==0:return 1.
    if p==1:return 0.
    terms=[math.lgamma(n+1)-math.lgamma(j+1)-math.lgamma(n-j+1)+j*math.log(p)+(n-j)*math.log1p(-p) for j in range(k+1)]
    largest=max(terms)
    return min(1.,math.exp(largest)*math.fsum(math.exp(t-largest) for t in terms))


def exact_binomial_interval(successes,trials,alpha):
    if type(trials) is not int or trials<1 or type(successes) is not int or not 0<=successes<=trials or not 0<alpha<1:
        raise ValueError('invalid exact binomial interval inputs')
    if successes==0:return 0.,-math.expm1(math.log(alpha/2)/trials)
    if successes==trials:return math.exp(math.log(alpha/2)/trials),1.
    def invert(k,target):
        low=0.;high=1.
        for _ in range(70):
            mid=(low+high)/2
            if _binomial_cdf(k,trials,mid)>target:low=mid
            else:high=mid
        return (low+high)/2
    lower=0. if successes==0 else invert(successes-1,1-alpha/2)
    upper=1. if successes==trials else invert(successes,alpha/2)
    return lower,upper


def closure_interval(effects,alpha):
    if not effects or any(type(x) not in (int,float) or x not in (-1,0,1) for x in effects):
        raise ValueError('closure requires paired binary differences in {-1,0,1}')
    n=len(effects);positive=sum(x==1 for x in effects);negative=sum(x==-1 for x in effects)
    plus=exact_binomial_interval(positive,n,alpha/2)
    minus=exact_binomial_interval(negative,n,alpha/2)
    return max(-1.,plus[0]-minus[1]),min(1.,plus[1]-minus[0])


def paired_interval(effects,endpoint,*,family_alpha=.05,primary_tests=1,target_halfwidth=None):
    if type(primary_tests) is not int or primary_tests<1 or not 0<family_alpha<1:raise ValueError('invalid interval family')
    assigned=len(effects)
    defined=[v for v in effects if type(v) in (int,float) and math.isfinite(v)]
    result={'assigned_worlds':assigned,'defined_pairs':len(defined),'undefined_pairs':assigned-len(defined),
            'family_alpha':family_alpha,'primary_tests':primary_tests,'precision_target':target_halfwidth}
    if len(defined)!=assigned or assigned<2:
        return result|{'mean_effect':None,'sd_effect':None,'standard_error':None,'lower':None,'upper':None,
                       'maximum_error_radius':None,'interval_kind':'undefined-all-assigned-world-estimand',
                       'precision_met':False,'estimand_status':'undefined; no world removed'}
    mean=statistics.fmean(defined);sd=statistics.stdev(defined);se=sd/math.sqrt(assigned)
    alpha=family_alpha/primary_tests
    if endpoint=='closed_by_common_end':
        low,high=closure_interval(defined,alpha);kind='paired discordance exact-binomial Bonferroni interval'
    else:
        critical=student_quantile(1-alpha/2,assigned-1);low=mean-critical*se;high=mean+critical*se
        kind='paired Student-t interval; approximate conditional Monte Carlo coverage'
    radius=max(mean-low,high-mean)
    return result|{'mean_effect':mean,'sd_effect':sd,'standard_error':se,'lower':low,'upper':high,
                   'maximum_error_radius':radius,'interval_kind':kind,
                   'precision_met':target_halfwidth is not None and radius<=target_halfwidth,
                   'estimand_status':'all assigned paired worlds retained'}


def planned_world_count(values,endpoint,epsilon,minimum,maximum,family_alpha,tests):
    if endpoint=='closed_by_common_end':
        if any(v not in (-1,0,1) for v in values):raise ValueError('invalid pilot closure difference')
        pplus=sum(v==1 for v in values)/len(values);pminus=sum(v==-1 for v in values)/len(values)
        for n in range(minimum,maximum+1):
            plus=round(n*pplus);minus=min(n-plus,round(n*pminus))
            projection=[1]*plus+[-1]*minus+[0]*(n-plus-minus)
            low,high=closure_interval(projection,family_alpha/tests);mean=(plus-minus)/n
            if max(mean-low,high-mean)<=epsilon:return n
        return maximum+1
    sd=statistics.stdev(values);n=minimum
    for _ in range(100):
        critical=student_quantile(1-family_alpha/(2*tests),n-1)
        requested=max(minimum,math.ceil((critical*sd/epsilon)**2))
        if requested<=n:return n
        n=requested
        if n>10_000_000:return n
    raise ArithmeticError('continuous precision planning did not converge')
