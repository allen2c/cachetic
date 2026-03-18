---
id: CAC-E02
title: "v0.6.0: General Performance Optimizations"
status: done
priority: medium
created: 2026-03-18
updated: 2026-03-18
issues:
  - CAC-004
  - CAC-005
  - CAC-006
---

# CAC-E02: v0.6.0 — General Performance Optimizations

## Overview

Beyond the MongoCache connection pooling (CAC-E01), several hot-path inefficiencies
exist in the core `Cachetic` class and compression utilities. Each is individually
small but compounds in high-frequency access patterns.

## Issues

| ID | Title | Status |
|---|---|---|
| CAC-004 | Reuse Zstd Compressor/Decompressor as module-level singletons | done |
| CAC-005 | Cache `_is_bytes_type` check at instance init | done |
| CAC-006 | Guard debug log calls to avoid eager `pretty_repr` evaluation | done |
