---
id: CAC-E01
title: "v0.6.0: MongoCache Connection Pooling"
status: done
priority: high
created: 2026-03-18
updated: 2026-03-18
issues:
  - CAC-001
  - CAC-002
  - CAC-003
---

# CAC-E01: v0.6.0 — MongoCache Connection Pooling

## Overview

Every `MongoCache` instance currently creates its own `pymongo.MongoClient`, even when
multiple instances share the same connection string. In applications with several
`Cachetic` instances backed by the same MongoDB server (e.g. AAO has 4 on the same
CosmosDB cluster), this causes:

- **Repeated SASL authentication handshakes** (~180–333ms each)
- **Redundant `createIndexes` commands** (~130–230ms each)
- **Wasted connection pool resources** (N pools instead of 1)

Measured overhead in AAO: **~811ms per request lifecycle** from 4 separate SASL sequences.

## Goal

Share a single `MongoClient` per connection string across all `MongoCache` instances.
Ensure indexes only once per `(database, collection)` pair per process lifetime.

## Issues

| ID | Title | Status |
|---|---|---|
| CAC-001 | MongoCache: Share MongoClient across instances via connection registry | done |
| CAC-002 | MongoCache: Deduplicate createIndexes calls | done |
| CAC-003 | MongoCache: Add tests for connection reuse behavior | done |
