import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'research_tools'))
from cloud_prices import build,unit_price
from cloud_control import GuardError


def catalog_fixture():
    snapshot=json.loads((ROOT/'docs/CLOUD_PRICE_SNAPSHOT.json').read_text())
    rows=[{'skuId':s['sku_id'],'description':s['description'],'category':s['category'],
           'serviceRegions':s['service_regions'],'pricingInfo':[{'effectiveTime':s['effective_time'],
                                                               'pricingExpression':s['pricing_expression']}]} for s in snapshot['selected_skus']]
    for role,description,nanos,group,sku_id,unit,factor in (
        ('cpu','M3 Memory-optimized Instance Core running in Americas',34800000,'CPU','2B4F-981A-703B','h',3600),
        ('ram','M3 Memory-optimized Instance Ram running in Americas',5100000,'RAM','D493-0B62-1972','GiBy.h',2**30*3600),
        ('disk','Balanced PD Capacity',100000000,'SSD','6AE1-525F-8B80','GiBy.mo',2**30*3600*744),
        ('disk','SSD backed PD Capacity',170000000,'SSD','B188-61DD-52E4','GiBy.mo',2**30*3600*744)):
        source=next(s for s in snapshot['selected_skus'] if s['role']==role)
        expression=copy.deepcopy(source['pricing_expression']);expression['usageUnit']=unit
        expression['baseUnitConversionFactor']=factor
        expression['tieredRates'][0]['unitPrice']['nanos']=nanos
        rows.append({'skuId':sku_id,'description':description,'category':{'resourceFamily':'Compute' if role!='disk' else 'Storage','resourceGroup':group,'usageType':'OnDemand'},
                     'serviceRegions':['us-central1'],'pricingInfo':[{'effectiveTime':'2026-10-07T07:00:00Z','pricingExpression':expression}]})
    machines=[{'name':f'm3-ultramem-{cpu}','guestCpus':cpu,'memoryMb':ram*1024} for cpu,ram in ((32,976),(64,1952),(128,3904))]
    return {'recorded_utc':'2026-10-08T08:00:00Z','skus':rows},machines


class CloudPriceTests(unittest.TestCase):
    def test_actual_public_catalog_units_and_conservative_arithmetic(self):
        p=json.loads((ROOT/'docs/CLOUD_PRICE_SNAPSHOT.json').read_text())
        cpu=next(s for s in p['selected_skus'] if s['role']=='cpu')
        ram=next(s for s in p['selected_skus'] if s['role']=='ram')
        self.assertEqual(ram['pricing_expression']['baseUnitConversionFactor'],3600000000000)
        rate=Decimal(cpu['price']);rr=Decimal(ram['price'])
        for name,m in p['machines'].items():
            nominal=Decimal(m['vcpus'])*rate+Decimal(m['memory_gib'])*rr
            byte=Decimal(m['vcpus'])*rate+Decimal(m['memory_mb_api'])*1024**2*3600/Decimal(3600000000000)*rr
            self.assertEqual(Decimal(p['spot_vm_usd_per_hour'][name]),max(nominal,byte))
        self.assertEqual(p['machines']['c4d-highmem-4']['memory_gib'],'31')
        self.assertEqual(p['machines']['c4d-highmem-384']['memory_gib'],'3024')
        self.assertEqual(Decimal(p['hyperdisk_rates']['capacity']['catalog_month_hours']),Decimal('744'))
        self.assertEqual(p['hyperdisk_rates']['capacity']['planning_month_hours'],'730')
        self.assertEqual(Decimal(p['spot_external_ip_hour']),Decimal('.0025'))

    def test_default_profile_preserves_retained_c4d_snapshot_shape_and_rates(self):
        retained=json.loads((ROOT/'docs/CLOUD_PRICE_SNAPSHOT.json').read_text())
        catalog,_=catalog_fixture();catalog['recorded_utc']=retained['checked_utc']
        machines=[{'name':name,'guestCpus':m['vcpus'],'memoryMb':m['memory_mb_api']} for name,m in retained['machines'].items()]
        rebuilt=build(catalog,machines)
        self.assertEqual(set(rebuilt),set(retained)-{'catalog_sha256','machine_catalog_sha256','additional_machine_catalog_sha256'})
        self.assertEqual(rebuilt['spot_vm_usd_per_hour'],retained['spot_vm_usd_per_hour'])
        self.assertEqual(rebuilt['hyperdisk_rates'],retained['hyperdisk_rates'])
        self.assertEqual(rebuilt['machines'],retained['machines'])

    def test_future_currency_and_multiple_tiers_refused(self):
        sku={'pricingInfo':[{'effectiveTime':'2026-10-07T00:00:00Z','pricingExpression':{'tieredRates':[{'startUsageAmount':0,'unitPrice':{'currencyCode':'USD','units':'0','nanos':1000}}]}}]}
        self.assertEqual(unit_price(sku,'2026-10-08T00:00:00Z')[0],Decimal('.000001'))
        with self.assertRaises(GuardError):unit_price(sku,'2026-10-06T00:00:00Z')
        bad=copy.deepcopy(sku);bad['pricingInfo'][0]['pricingExpression']['tieredRates'][0]['unitPrice']['currencyCode']='EUR'
        with self.assertRaises(GuardError):unit_price(bad,'2026-10-08T00:00:00Z')
        bad=copy.deepcopy(sku);bad['pricingInfo'][0]['pricingExpression']['tieredRates']*=2
        with self.assertRaises(GuardError):unit_price(bad,'2026-10-08T00:00:00Z')

    def test_explicit_m3_standard_catalog_uses_ordinary_on_demand_gib_hour(self):
        catalog,machines=catalog_fixture()
        result=build(catalog,machines,profile='m3-standard')
        self.assertEqual(result['purchase_mode'],'STANDARD')
        self.assertEqual(result['machine_family'],'M3')
        self.assertNotIn('spot_vm_usd_per_hour',result)
        self.assertNotIn('spot_external_ip_hour',result)
        self.assertEqual(result['standard_vm_usd_per_hour'],{'m3-ultramem-32':'6.0912','m3-ultramem-64':'12.1824','m3-ultramem-128':'24.3648'})
        self.assertEqual(result['standard_vm_usd_per_hour'],result['standard_vm_catalog_bytequantity_usd_per_hour'])
        self.assertEqual(result['standard_vm_usd_per_hour'],result['standard_vm_nominal_jedec_usd_per_hour'])
        self.assertEqual(Decimal(result['standard_external_ip_hour']),Decimal('.005'))
        self.assertEqual(set(result['disk_gib_hour']),{'pd-balanced','pd-ssd','hyperdisk-balanced'})
        self.assertEqual(Decimal(result['persistent_disk_rates']['pd-balanced']['planning_usd_per_hour_upper']),Decimal('.1')/730)
        self.assertEqual(next(x for x in result['selected_skus'] if x['role']=='ram')['pricing_expression']['baseUnitConversionFactor'],2**30*3600)
        # A retained singleton machine describe response is enough to price only
        # that observed shape; it does not claim the other shapes were observed.
        one=build(catalog,{'exit_code':0,'stdout':json.dumps(machines[-1])},profile='m3-standard')
        self.assertEqual(set(one['standard_vm_usd_per_hour']),{'m3-ultramem-128'})
        retained=build(catalog,{'exit_code':0,'data':machines},profile='m3-standard')
        self.assertEqual(result['standard_vm_usd_per_hour'],retained['standard_vm_usd_per_hour'])

    def test_standard_never_substitutes_spot_sole_tenant_commitment_or_megamem(self):
        original,machines=catalog_fixture()
        cpu_description='M3 Memory-optimized Instance Core running in Americas'
        for mutation in ('Spot','Commit1Yr','sole-tenant','M2','missing','duplicate','future','wrong-region','wrong-unit','wrong-conversion'):
            catalog=copy.deepcopy(original)
            row=next(s for s in catalog['skus'] if s['description']==cpu_description)
            if mutation in ('Spot','Commit1Yr'):row['category']['usageType']=mutation
            elif mutation=='sole-tenant':row['description']='M3 Memory-optimized Sole Tenancy Instance Core running in Americas'
            elif mutation=='M2':row['description']='M2 Memory-optimized Instance Core running in Americas'
            elif mutation=='missing':catalog['skus'].remove(row)
            elif mutation=='duplicate':catalog['skus'].append(copy.deepcopy(row))
            elif mutation=='future':row['pricingInfo'][0]['effectiveTime']='2026-10-09T00:00:00Z'
            elif mutation=='wrong-region':row['serviceRegions']=['us-east1']
            elif mutation=='wrong-unit':row['pricingInfo'][0]['pricingExpression']['usageUnit']='GiBy.h'
            elif mutation=='wrong-conversion':
                ram=next(s for s in catalog['skus'] if s['description']=='M3 Memory-optimized Instance Ram running in Americas')
                ram['pricingInfo'][0]['pricingExpression']['baseUnitConversionFactor']=3600000000000
            with self.subTest(mutation=mutation),self.assertRaises(GuardError):build(catalog,machines,profile='m3-standard')
        for mutation in ('megamem','custom-memory','wrong-cpu','failed-acquisition'):
            supplied=copy.deepcopy(machines[-1])
            if mutation=='megamem':supplied['name']='m3-megamem-128'
            elif mutation=='custom-memory':supplied['memoryMb']=1952*1024
            elif mutation=='wrong-cpu':supplied['guestCpus']=64
            elif mutation=='failed-acquisition':supplied={'exit_code':1,'stdout':json.dumps(machines)}
            with self.subTest(mutation=mutation),self.assertRaises(GuardError):build(original,supplied,profile='m3-standard')
        with self.assertRaises(GuardError):build(original,machines,profile='m3-spot')
        with self.assertRaises(GuardError):build(original,machines,region='us-east1',profile='m3-standard')

    def test_catalog_negative_rate_and_invalid_monthly_conversion_fail_closed(self):
        original,machines=catalog_fixture()
        for mutation in ('negative','nan','invalid-nanos','zero-month','wrong-month-unit'):
            catalog=copy.deepcopy(original)
            row=next(s for s in catalog['skus'] if s['description']=='Balanced PD Capacity')
            exp=row['pricingInfo'][0]['pricingExpression']
            if mutation=='negative':exp['tieredRates'][0]['unitPrice']['units']='-1'
            elif mutation=='nan':exp['tieredRates'][0]['unitPrice']['units']='NaN'
            elif mutation=='invalid-nanos':exp['tieredRates'][0]['unitPrice']['nanos']=1000000000
            elif mutation=='zero-month':exp['baseUnitConversionFactor']=0
            elif mutation=='wrong-month-unit':exp['usageUnit']='GBy.mo'
            with self.subTest(mutation=mutation),self.assertRaises(GuardError):build(catalog,machines,profile='m3-standard')
if __name__=='__main__':unittest.main()
