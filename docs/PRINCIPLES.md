# Principles

The constitution. Every release obeys every line.

This file exists because an unwritten rule is a broken rule. Anyone working
under a task — human or agent — will find a good reason to break one, and a good
reason is not an exception. If a task cannot be done without breaking a rule,
the task is wrong, not the rule. Amend a rule here first, in a change of its
own, or not at all.

Rules are ordered: a later rule never overrides an earlier one.

1. Every version reads every value an earlier version wrote, and accepts every call v0.6.0 or later accepted.
2. One API: every client offers it whole, every backend answers it the same; a difference that cannot be removed is documented, never silent.
3. TTL is one number with three meanings: negative never expires (the default), positive expires after N seconds, and 0 means do not cache — `default_ttl=0` turns the client off, a per-call `ex=0` drops that one write, and neither deletes what is already stored.
4. Keys are built one way only — `prefix:key`, with nothing inserted — because changing the scheme orphans every value already cached.
5. `pip install cachetic` works alone: every backend package is optional, imported only when its URL scheme is used.
