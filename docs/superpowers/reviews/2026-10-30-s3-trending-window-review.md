# S3 trending window: review notes

## Measurements (Task 1, Step 7; spec N7)

`ImageRepository.get_trending_window`, first page (limit 24), on a private scratch database (created and dropped on
the test server, schema from `Base.metadata.create_all`, `ANALYZE` after loading).

- Data: 30,000 active images; 200,000 open absorb edges (weights 1 to 3, `created_at` uniform over 400 days, so 15,037
  edges inside the 30-day window); 1,000 of the edges are chain links (image k absorbed into image k-1) so the climb
  has real depth; the rest have random winners and fresh loser ids (deleted losers).
- Method: 3 warm-up calls, then 20 timed calls through the repository method on one session.
- Result: p50 136.7 ms, p95 160.2 ms (the 19th of 20), max 164.2 ms. Under the 300 ms budget, so no index and no
  migration. The cost is the sequential scan of the log for the seed (no index on `created_at`).
