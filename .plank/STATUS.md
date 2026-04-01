# Cachetic — STATUS

Last reviewed: 2026-04-01

## Active

| ID      | Title                                                           | Status | Stopped at |
|---------|-----------------------------------------------------------------|--------|------------|
| CAC-014 | Add version compatibility tests for Data URL and legacy formats | todo   | —          |

## Recently Done

| ID      | Title                                                                    | Completed  |
|---------|--------------------------------------------------------------------------|------------|
| CAC-010 | Implement Postgres backend extension                                     | 2026-04-01 |
| CAC-007 | Unify backend architecture with CacheProtocol adapters                   | 2026-04-01 |
| CAC-008 | Make Redis an optional extension                                         | 2026-04-01 |
| CAC-009 | Implement Data URL serialization format with backward compat             | 2026-04-01 |
| CAC-011 | Expand CacheProtocol with `exists` and `clear` methods                   | 2026-04-01 |
| CAC-012 | Make `rich` a conditional import                                         | 2026-04-01 |
| CAC-013 | Scope `str_or_none` to mongodb optional dependency                       | 2026-04-01 |
| CAC-001 | MongoCache: Share MongoClient across instances via connection registry    | 2026-03-18 |
| CAC-002 | MongoCache: Deduplicate createIndexes calls                              | 2026-03-18 |
| CAC-003 | MongoCache: Add tests for connection reuse behavior                      | 2026-03-18 |
| CAC-004 | Reuse Zstd Compressor/Decompressor as module-level singletons           | 2026-03-18 |
| CAC-005 | Cache _is_bytes_type check at instance init                              | 2026-03-18 |
| CAC-006 | Guard debug log calls to avoid eager pretty_repr evaluation              | 2026-03-18 |
