# Performance benchmark — 2026-10-09

## Environment and method

Windows PowerShell;Python3.11.9;Node24.21.0;Docker29.8.1/Compose5.5.1,localPG16/Moto5.2.3. Host12logical CPUs/~15.34GiB RAM;Docker memory budget~7.43GiB. Runtime versions/image hashes in `final-runtime/results.json`;backend dependencies are not fully locked. Measurements are local observations,not production SLA.

Only five10k/seed-7 datasets. Raw mapping/decoder preserves IDs/source/provenance;CSV/CP1258/mixed/XLSX decoding here is QA adapter support. Ground truth read only AFTER computation;recall/auto-merge evaluation uses CERTAIN labels among decoded accepted records. TC5 has10010accepted+9rejected logical rows,30missing names;offline success does not imply native API support.

`qa_benchmark.py` serial StageRunner measurements:wall/CPU and50ms RSS sampling of one host process,not whole-machine/container peak. Background Docker/test/build load can affect wall time;do not infer capacity at21k/100k/1M. Final stage runs use stable source-derived IDs. API imports create new UUIDs and may have TF-IDF tie-break count variation.

## Baseline and final stages

| Dataset | Records | Baseline wall s / CPU s / RSS GiB | Final wall s / CPU s / RSS GiB | Candidates | Proposals | Candidate recall | Auto false pairs (CERTAIN) |
|---|---:|---|---|---:|---:|---|---:|
| TC1_easy_match | 10022 | 97.812/94.438/1.103 | 35.578/36.734/1.118 | 83124 | 2190 | 100.00% (4894/4894) | 0 |
| TC2_easy_non_match | 10000 | 52.859/38.828/0.902 | 28.765/29.562/0.865 | 72213 | 0 | N/A(no positive labels) | 0 |
| TC3_hard_match | 9992 | 33.391/34.109/0.741 | 35.625/36.438/0.655 | 90129 | 0 | 66.94% (3256/4864) | 0 |
| TC4_hard_non_match | 10014 | 32.375/33.125/0.976 | 35.485/35.859/0.824 | 79522 | 0 | N/A(no positive labels) | 0 |
| TC5_real_world | 10010 | 42.813/43.047/0.858 | 33.891/34.938/0.738 | 85492 | 355 | 91.48% (9680/10582) | 0 |

Final duration in milliseconds per stage:

| Dataset | PREPARE | NORMALIZE | CANDIDATES | SCORE | DECIDE | CLUSTER | MATERIALIZE |
|---|---:|---:|---:|---:|---:|---:|---:|
| TC1_easy_match | 641 | 1311 | 4735 | 12000 | 14875 | 1546 | 0 |
| TC2_easy_non_match | 156 | 1203 | 3750 | 11686 | 10453 | 1203 | 0 |
| TC3_hard_match | 141 | 1203 | 3889 | 20437 | 9375 | 484 | 0 |
| TC4_hard_non_match | 141 | 1296 | 4186 | 13953 | 14546 | 1078 | 0 |
| TC5_real_world | 140 | 1078 | 3938 | 17578 | 10155 | 703 | 0 |

The sum of stages excludes parts of runner setup/validation/evaluation. Throughput must use the matching stage denominator,not total runtime. `benchmark-results.json` contains counts/timings/candidate metrics/CPU/RSS for calculations. Zero observed CERTAIN false auto pairs is not perfect overall matching;TC1auto pair recall44.75%,TC3zero,TC5~3.52%.

## Verified optimization and changes

Original review-lookup-only attempt103.328s vs97.812baseline:hashes equal,NO speed improvement. Later incident-pair guard index + one policy load:TC1total97.812→34.437s(~2.84x);CLUSTER60.171→1.687s(~35.7x),same83124candidates,2190proposals,exact decision+cluster hashes. RSS1.103→1.202GiB in that run,about100MiB index tradeoff. Evidence `optimization-comparison.json` and `guard-index-optimized/TC1_easy_match/measurement.json`.

Final calendar-validation runTC135.578s,cluster1.546s,RSS1.118GiB. Full final decision hash differs because scoring_model_hash pins changed source. Join-by-pair-ID comparison proves TC1–4business fields unchanged,only model fingerprint changed. TC5has4affected evidence/comparison/signature rows,3subgroup rows and1score from real calendar validation;candidate sets/recall and primary decisions preserved. See `final-decision-differences.json`;do not call the literal final decision hash equal to baseline.

## Retrieval quality and complexity

Synthetic A/B/C across batches1/5/8 and CRM/ERP/BILL is found through global index,with dedup/scoring assertions. Real label coverage still incomplete:TC3cross-batch3256/4864;TC5cross-batch7395/8212 andcross-source8781/9607. TC3misses1608,TC5misses902. Shared contacts and oversized buckets have caps;skipped_pair_observations counts overlapping blocking observations,not unique lost positives.

Global sparse union probe retains every baseline candidate:TC3140997candidates,recall76.15%,generation9.938s;TC5132628candidates,recall95.87%,generation6.797s. Still1160/437misses. This probe is not default optimization. Global/partition sparse similarity can approach all-pairs work BEFORE Top-K;budget controls exist but Top-K alone is not complexity proof. Default index/candidate union are still in RAM;no1Mscalability claim. Comparison reduction ratio is~99.8%,but high reduction does not excuse recall loss.

## Actual integration and infrastructure timing

Latest final-image local worker smoke:100records4.109s,1k8.282s,4k23.609s pipeline timers;TC110022records98.734s,83144candidates,2190proposals. An earlier optimized-imageTC1run63.954s. These include queue/persistence/shared-load and random IDs,so cannot be used as controlled speedup.

V2+streaming+resume100/1k completed with15/68checkpoints;timers7.157/24.375s. V210022completed474.531s,17153serving pairs,23proposals. Different constraints/semantics mean it is not a like-for-like accuracy/performance comparison. Checkpoint/partition ledger throughput dominated the observed long run;needs focused profiling before performance release.

Local100query samplesP50/P95/P99=2.764/3.601/5.373ms,poolcheckedout0,EXPLAINrun-id index/top-N. Neon9queries withconcurrency4:firstconnection5.766s,subsequentmin/median/max1031/1078/2484ms. Too few Neon samples forP95/P99/SLA;serverless cold-state unverified.

RealR211.7MBmultipartupload9.453s(~1.24MB/s decimal payload);checksum/range+download case15.625s. Parquet1000rows1.453s;eightobjectconcurrency4case2.125s. Local10.5MBmultipart~0.234s in latest smoke. Network/storage locations differ,so no local-vs-cloud speed ratio claim.

Frontend production images/real browser worked,with paginated lists and no console errors in covered flows. No crediblefrontendP95/P99/FCP/large-result render benchmark;NOT RUN. Docker idle resource snapshot is not peak/leak proof. Concurrent uploads/two workers/doubleexecute and killed-child recovery passed;no sustained soak or service-wide latency distribution. Upload request timings are available in local smoke `requests`,butparse-only latency/peak ingestion memory were not independently instrumented.

## Scope limits

21k/100k/1MNOT RUN per user instruction. The historical21k268261pairs/25.33s/1.03GiB/16357golden reference is not compared because dataset/policy/environment differ. Candidate quality and v2 throughput block readiness despite successful execution. CPU/RAM/disk/queue/service-wide measurements still need sustained workload instrumentation.
