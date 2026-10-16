# TEST FIXTURES ONLY — NOT REAL DHAKA DATA

Every value in this folder is synthetic and exists only for unit tests
(`backend/app/tests/`). Fares, distances, times, wealth indices and coordinates are
deliberately fake: coordinates sit near 0°,0° and are not in Dhaka.

- Never copy these files into `backend/`, `backend/datasets/` or `backend/co-ordination/`.
- Production inspection/normalization never reads this folder. It is used only with
  `--source fixtures`, which writes to `backend/data/generated/fixtures/`.
- Each file name contains `TEST_FIXTURE`. A fixture that turns up among production
  files is flagged and skipped.

Contents: 3 hubs (Uttara North, Mirpur 10, Farmgate), 3 route edges (2 headerless
polyline rows and 1 headered edge), 1 metro matrix (3×3), 2 mode rates, 2 multiplier
rows, 1 wealth index and 1 student rule.
