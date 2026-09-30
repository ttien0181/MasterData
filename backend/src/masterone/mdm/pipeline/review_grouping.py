from hashlib import sha256

GROUPS = ("CONSTRAINT_OR_CLUSTER_RISK", "SINGLE_MAJOR_CONFLICT", "SHARED_VALUE",
          "VIETNAM_VARIANT", "SPARSE_EVIDENCE", "MODEL_DISAGREEMENT")


def classify_pair(pair: dict, record_by_id: dict[str, dict]) -> dict | None:
    if pair.get("triage_status") != "REVIEW":
        return None
    evidence = {item["field"]: item for item in pair.get("field_evidence", [])}
    left, right = record_by_id[pair["left_id"]], record_by_id[pair["right_id"]]
    reason = pair.get("triage_reason", "")
    shared = [field for field in ("phone", "email")
              if evidence.get(field, {}).get("similarity") == 1.0]
    different = [field for field in ("name", "phone", "email", "address")
                 if evidence.get(field, {}).get("similarity", 0) < 0.4
                 and (left["normalized_fields"].get(field) or right["normalized_fields"].get(field))]
    if reason in {"CLUSTER_RISK", "CLUSTER_NOT_CONFIRMED"}:
        group, subgroup = GROUPS[0], "TRANSITIVE_RISK"
    elif shared:
        group, subgroup = "SHARED_VALUE", "SHARED_PHONE" if "phone" in shared else "GENERIC_EMAIL"
    elif len(different) == 1:
        group, subgroup = "SINGLE_MAJOR_CONFLICT", different[0].upper()
    elif any(evidence.get(field, {}).get("similarity", 0) >= 0.8 for field in ("name", "address")):
        group, subgroup = "VIETNAM_VARIANT", "TEXT_VARIANT"
    elif sum(bool(value) for value in left["normalized_fields"].values()) < 3 or sum(
        bool(value) for value in right["normalized_fields"].values()) < 3:
        group, subgroup = "SPARSE_EVIDENCE", "LIMITED_FIELDS"
    else:
        group, subgroup = "MODEL_DISAGREEMENT", "OTHER_UNCERTAIN"
    signature = f"mdm-person-review-v1:{group}:{subgroup}"
    return {"primary_group": group, "subgroup": subgroup,
            "signature_hash": sha256(signature.encode()).hexdigest(), "reason_codes": [reason]}
