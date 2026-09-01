# Hikvision Video Intercom

A local integration for Hikvision door stations (DS-KB / DS-KV / DS-KD),
speaking **pure ISAPI over HTTP** — no Hikvision SDK binary and no companion
server.

Detects the doorbell reliably even on models whose `alertStream` is broken from
the factory, using layered channels with automatic fallback.
