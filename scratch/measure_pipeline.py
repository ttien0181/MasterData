import sys
import time
import json
import hashlib
from masterone.mdm.pipeline.stage_runner import StageRunner

def make_10k_records():
    records = []
    for i in range(10000):
        # 10000 records total.
        # name shared by 50 records. 200 distinct names.
        name = f"nguyen van {i // 50}" 
        # phone shared by 5 records
        phone = f"09{(i//5):08d}"
        records.append({
            "id": f"r{i:05d}",
            "entity_type": "PERSON",
            "raw_fields": {"name": name, "phone": phone, "email": "", "address": ""},
            "normalized_fields": {
                "name": name, "name_unicode": name,
                "phone": phone,
                "phones": [{"value": phone, "quality": "VALID", "is_primary": True}],
                "email": "", "emails": [], "address": "", "address_parts": {},
                "_field_quality": {"name": "VALID", "phone": "VALID", "email": "MISSING",
                                   "address": "MISSING", "dob": "MISSING", "government_id": "MISSING"},
                "_name_parts": {"surname": "nguyen", "middle_name": "van", "given_name": f"{i // 50}"},
                "dob": f"{1970 + (i % 50)}-01-01",
            },
            "quality_flags": [],
        })
    return records

def serialize_and_hash(obj):
    s = json.dumps(obj, sort_keys=True, ensure_ascii=False).encode('utf-8')
    return hashlib.sha256(s).hexdigest()

def main():
    records = make_10k_records()
    print(f"Generated {len(records)} records.")
    
    runner = StageRunner(run_id="perf_run_10k", attempt_id="a1")
    
    start_time = time.time()
    output = runner.run_all(records)
    total_time = time.time() - start_time
    
    print("\n--- STAGE TIMES ---")
    for sr in runner.stage_results:
        print(f"{sr.stage_name:20s}: {sr.duration_ms / 1000:.3f} s")
    print(f"Total time: {total_time:.3f} s")
    
    # Sort pairs by id to ensure deterministic order if process pool changes order
    if isinstance(output.get("pairs"), list):
        output["pairs"].sort(key=lambda x: (x.get("left_id", ""), x.get("right_id", "")))

    print("\n--- HASHES ---")
    print("pairs:        ", serialize_and_hash(output["pairs"]))
    print("proposals:    ", serialize_and_hash(output["proposals"]))
    print("review_cases: ", serialize_and_hash(output["review_cases"]))
    
    with open("scratch/perf_baseline.json", "w") as f:
        json.dump({
            "stages": {sr.stage_name: sr.duration_ms for sr in runner.stage_results},
            "hashes": {
                "pairs": serialize_and_hash(output["pairs"]),
                "proposals": serialize_and_hash(output["proposals"]),
                "review_cases": serialize_and_hash(output["review_cases"])
            }
        }, f)

if __name__ == "__main__":
    main()
