---
id: CAC-E03
title: "v0.7.0: Backend architecture refactor and Data URL format"
status: in-progress
priority: high
created: 2026-04-01
updated: 2026-04-01
---

# v0.7.0: Backend architecture refactor and Data URL format

## Overview

Refactor cachetic to unify all cache backends behind `CacheProtocol` adapters,
introduce self-describing Data URL serialization via `durl-py`, add a Postgres
backend, and make Redis / rich optional dependencies.

Key constraint: **maximum backward compatibility**. Existing clients upgrading
to v0.7.0 must experience zero breakage — their databases, stored data, and
API call patterns all remain valid.

## Issues

| ID      | Title                                                        | Status |
|---------|--------------------------------------------------------------|--------|
| CAC-007 | Unify backend architecture with CacheProtocol adapters       | done   |
| CAC-008 | Make Redis an optional extension                             | done   |
| CAC-009 | Implement Data URL serialization format with backward compat | done   |
| CAC-010 | Implement Postgres backend extension                         | done   |
| CAC-011 | Expand CacheProtocol with `exists` and `clear` methods       | done   |
| CAC-012 | Make `rich` a conditional import                             | done   |
| CAC-013 | Scope `str_or_none` to mongodb optional dependency           | done   |
| CAC-014 | Add version compatibility tests for Data URL and legacy formats | todo   |
