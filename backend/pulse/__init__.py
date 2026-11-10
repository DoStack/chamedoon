"""Pulse: the read-only analytics pages under /admin/pulse/.

Runs as its own Django process on the Chamedoon VM (see docs/pulse.md). Vercel only forwards /admin/pulse/ to it.
Every query goes through the `pulse_ro` Postgres role, which cannot write, so nothing here can change data.
"""
