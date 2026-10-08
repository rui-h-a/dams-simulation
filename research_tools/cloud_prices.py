"""Parse retained official Billing Catalog SKUs; never query private credentials.

Compute's pricing documentation defines its machine-memory GB as binary GiB.
Catalog raw units/base conversion are retained, rather than silently rewritten.
Monthly disk rates preserve the API's conversion and a conservative 730h plan.
"""
from __future__ import annotations
import argparse
from decimal import Decimal
import hashlib
import json
from pathlib import Path
from cloud_control import GuardError, atomic, money, utc


def unit_price(sku, checked):
    current = [p for p in sku["pricingInfo"] if utc(p["effectiveTime"]) <= utc(checked)]
    if not current:
        raise GuardError("SKU has no effective price at acquisition time")
    p = max(current, key=lambda p: utc(p["effectiveTime"]))
    rates = p["pricingExpression"]["tieredRates"]
    if len(rates) != 1 or money(rates[0]["startUsageAmount"]) != 0:
        raise GuardError("selected fixed rate requires an explicit zero-start single tier")
    v = rates[0]["unitPrice"]
    if v["currencyCode"] != "USD":
        raise GuardError("only exact USD ledger rates are accepted")
    amount = Decimal(v.get("units", "0")) + Decimal(v.get("nanos", 0)) / 1_000_000_000
    return amount, p


def select(rows, region, description, usage):
    matches = [r for r in rows if region in r["serviceRegions"] and r["description"] == description
               and r["category"]["usageType"] == usage]
    if len(matches) != 1:
        raise GuardError("expected exactly one matching region/category/description SKU: " + description)
    return matches[0]


def build(catalog, machines, region="us-central1"):
    checked, rows = catalog["recorded_utc"], catalog["skus"]
    if region != "us-central1":
        raise GuardError("this checked source map is Iowa-only; re-price other regions explicitly")
    cpu = select(rows, region, "Spot Preemptible C4D Instance Core running in Americas", "Preemptible")
    ram = select(rows, region, "Spot Preemptible C4D Instance Ram running in Americas", "Preemptible")
    disk = select(rows, region, "Hyperdisk Balanced Capacity in Iowa", "OnDemand")
    iops = select(rows, region, "Hyperdisk Balanced IOPS in Iowa", "OnDemand")
    throughput = select(rows, region, "Hyperdisk Balanced Throughput in Iowa", "OnDemand")
    rates, retained = {}, []
    for key, sku in (("cpu", cpu), ("ram", ram), ("disk", disk), ("iops", iops), ("throughput", throughput)):
        amount, info = unit_price(sku, checked)
        rates[key] = amount
        retained.append({"role": key, "sku_id": sku["skuId"], "description": sku["description"],
                         "category": sku["category"], "service_regions": sku["serviceRegions"],
                         "price": str(amount), "effective_time": info["effectiveTime"],
                         "pricing_expression": info["pricingExpression"]})
    if retained[0]["pricing_expression"]["usageUnit"] != "h" or retained[1]["pricing_expression"]["usageUnit"] not in ("GBy.h", "GiBy.h"):
        raise GuardError("unexpected C4D CPU/memory usage unit")
    vm, nominal, byte_quantity = {}, {}, {}
    if isinstance(machines, dict) and "stdout" in machines:
        if machines["exit_code"] != 0:
            raise GuardError("machine catalog acquisition failed")
        machines = json.loads(machines["stdout"])
    supported = {}
    for m in machines:
        if m["name"] not in ("c4d-highmem-4", "c4d-highmem-96", "c4d-highmem-192", "c4d-highmem-384"):
            continue
        cpu_count, memory_gib = m["guestCpus"], Decimal(m["memoryMb"]) / 1024
        if m["name"] in supported and supported[m["name"]]["memory_gib"] != str(memory_gib):
            raise GuardError("machine memory differs by zone")
        supported[m["name"]] = {"vcpus": cpu_count, "memory_gib": str(memory_gib), "memory_mb_api": m["memoryMb"]}
        nominal[m["name"]] = str(cpu_count * rates["cpu"] + memory_gib * rates["ram"])
        memory_bytes = Decimal(m["memoryMb"]) * 1024**2
        ram_exp = retained[1]["pricing_expression"]
        ram_billed_hours = memory_bytes * 3600 / Decimal(str(ram_exp["baseUnitConversionFactor"]))
        byte_quantity[m["name"]] = str(cpu_count * rates["cpu"] + ram_billed_hours * rates["ram"])
        vm[m["name"]] = str(max(Decimal(nominal[m["name"]]), Decimal(byte_quantity[m["name"]])))
    if not {"c4d-highmem-96", "c4d-highmem-192", "c4d-highmem-384"}.issubset(vm):
        raise GuardError("all three frozen C4D highmem targets require actual machine catalog evidence")
    def monthly(role, gib=False):
        entry = next(x for x in retained if x["role"] == role)
        exp = entry["pricing_expression"]
        if exp["usageUnit"] != ("GiBy.mo" if gib else "mo"):
            raise GuardError("unexpected Hyperdisk monthly rate units")
        api_hours = Decimal(str(exp["baseUnitConversionFactor"])) / (Decimal(2**30) if gib else 1) / 3600
        return {"catalog_month_hours": str(api_hours), "catalog_usd_per_hour": str(rates[role] / api_hours),
                "planning_month_hours": "730", "planning_usd_per_hour_upper": str(max(rates[role] / api_hours, rates[role] / 730))}
    hd = {"capacity": monthly("disk", True), "extra_iops": monthly("iops"), "extra_throughput_mibps": monthly("throughput")}
    return {"version": 1, "currency": "USD", "region": region, "checked_utc": checked,
            "spot_vm_usd_per_hour": vm, "spot_vm_nominal_jedec_usd_per_hour": nominal,
            "spot_vm_catalog_bytequantity_usd_per_hour": byte_quantity, "machines": supported,
            "hyperdisk_gib_hour": hd["capacity"]["planning_usd_per_hour_upper"], "hyperdisk_rates": hd,
            "gcs_gib_hour": "0.000027397", "egress_gib": "0.12", "spot_external_ip_hour": "0.0025",
            "gcs_class_a_per_1000": "0.005", "gcs_class_b_per_1000": "0.0004",
            "selected_skus": retained,
            "sources": ["https://cloudbilling.googleapis.com/v1/services/6F81-5844-456A/skus",
                        "https://cloud.google.com/products/compute/pricing",
                        "https://cloud.google.com/compute/disks-image-pricing", "https://cloud.google.com/storage/pricing",
                        "https://cloud.google.com/vpc/network-pricing"],
            "interpretation": ["Compute pricing defines memory GB as JEDEC binary GiB. Raw C4D catalog GBy.h has decimal byte conversion; both interpretations are preserved and their maximum is reserved, not asserted as an exact invoice price.",
                               "Current Spot rates are not fixed for future launches; refresh within 24h and reserve an additional private safety margin.",
                               "Catalog monthly disk base-unit conversion is retained; max(API hourly, monthly/730) is the conservative planning rate.",
                               "GCS values are official Iowa Standard flat-namespace/current web table; Asia excluding China download charged at first tier without free allowances.",
                               "No VM runtime, availability, guest memory, billing invoice or cloud performance is certified by catalog inspection."]}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--catalog", required=True, type=Path)
    p.add_argument("--machines", required=True, type=Path)
    p.add_argument("--additional-machines", type=Path, help="Additional retained actual machine-catalog response")
    p.add_argument("--output", required=True, type=Path)
    a = p.parse_args()
    machines = json.loads(a.machines.read_text())
    if a.additional_machines:
        extra = json.loads(a.additional_machines.read_text())
        def unwrap(value):
            if isinstance(value, dict) and "stdout" in value:
                if value["exit_code"] != 0: raise GuardError("additional catalog acquisition failed")
                value = json.loads(value["stdout"])
            return value if isinstance(value, list) else [value]
        machines = [*unwrap(machines), *unwrap(extra)]
    result = build(json.loads(a.catalog.read_text()), machines)
    result["catalog_sha256"] = hashlib.sha256(a.catalog.read_bytes()).hexdigest()
    result["machine_catalog_sha256"] = hashlib.sha256(a.machines.read_bytes()).hexdigest()
    if a.additional_machines:
        result["additional_machine_catalog_sha256"] = hashlib.sha256(a.additional_machines.read_bytes()).hexdigest()
    atomic(a.output, result)
    print(json.dumps({"spot_vm_usd_per_hour": result["spot_vm_usd_per_hour"], "hyperdisk_gib_hour": result["hyperdisk_gib_hour"]}, indent=2))


if __name__ == "__main__": main()
