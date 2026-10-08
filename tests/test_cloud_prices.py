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

    def test_future_currency_and_multiple_tiers_refused(self):
        sku={'pricingInfo':[{'effectiveTime':'2026-10-07T00:00:00Z','pricingExpression':{'tieredRates':[{'startUsageAmount':0,'unitPrice':{'currencyCode':'USD','units':'0','nanos':1000}}]}}]}
        self.assertEqual(unit_price(sku,'2026-10-08T00:00:00Z')[0],Decimal('.000001'))
        with self.assertRaises(GuardError):unit_price(sku,'2026-10-06T00:00:00Z')
        bad=copy.deepcopy(sku);bad['pricingInfo'][0]['pricingExpression']['tieredRates'][0]['unitPrice']['currencyCode']='EUR'
        with self.assertRaises(GuardError):unit_price(bad,'2026-10-08T00:00:00Z')
        bad=copy.deepcopy(sku);bad['pricingInfo'][0]['pricingExpression']['tieredRates']*=2
        with self.assertRaises(GuardError):unit_price(bad,'2026-10-08T00:00:00Z')

if __name__=='__main__':unittest.main()
