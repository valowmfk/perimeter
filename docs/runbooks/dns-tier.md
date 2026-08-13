# DNS Tier Runbook — Pi-hole + nebula-sync

How local DNS records reach the resolvers your clients actually use, and how to
keep the primary → replica sync healthy.

## Topology

```
Perimeter provisioning ──PUT /api/config/dns/hosts──▶ Pi-hole PRIMARY (10.1.55.9, pihole09)
                                                             │
                                                    nebula-sync (full config? no — selective)
                                                             │  PATCH /api/config
                                              ┌──────────────┴──────────────┐
                                              ▼                              ▼
                                   Pi-hole REPLICA (10.1.55.10)   Pi-hole REPLICA (10.1.55.11)
                                        ⇧ in-use resolvers your clients point at ⇧
```

- **Primary** is the only node Perimeter writes to. `helpers/dns_manager.py` adds
  records via `PUT /api/config/dns/hosts/{IP FQDN}` on `PIHOLE_API_URL`.
- **Replicas** (`.10`, `.11`) are what clients actually query. They receive records
  from the primary **only** via nebula-sync. If nebula-sync is broken, a freshly
  provisioned host resolves on the primary but nowhere clients look — the classic
  "the workflow stopped creating DNS records" false alarm.

## Golden rules (learned the hard way)

1. **Pin every Pi-hole to the same explicit image tag, and update them as a set.**
   `:latest` drift is the #1 cause of sync failure — a replica recreated ahead of
   the primary pulls a newer FTL whose config schema the older primary can't produce
   (or vice versa), and `PATCH /api/config` 400s. All nodes on the *same* FTL version
   is a hard requirement for config sync.
2. **Keep `SYNC_CONFIG_*_EXCLUDE` in lockstep with the replicas' `FTLCONF_*` env vars.**
   Pi-hole v6 refuses API writes to any config item set via an `FTLCONF_*` environment
   variable (`400 bad_request: "Config items set via environment variables cannot be
   changed via the API"`). nebula-sync sends the whole section in one PATCH, so a
   single env-pinned key rejects the entire sync — and nebula-sync aborts before
   reaching the next replica.

## nebula-sync configuration

Selective sync (not full), excluding the env-pinned `dns` keys. This preserves the
replicas' intentional per-node config (upstreams, reply-host, API password) while
still propagating local records (`dns.hosts`):

```env
PRIMARY=https://10.1.55.9|<password>
REPLICAS=https://10.1.55.10|<password>,https://10.1.55.11|<password>
FULL_SYNC=false
CLIENT_RETRY_DELAY_SECONDS=20
SYNC_CONFIG_DNS=true
SYNC_CONFIG_RESOLVER=false
# Must list every FTLCONF_-pinned key in the dns section (see "find env-pinned keys").
# Keys are relative to the section and CASE-SENSITIVE (note capital IPv4):
SYNC_CONFIG_DNS_EXCLUDE=upstreams,reply.host.force4,reply.host.IPv4
```

Notes:
- `FULL_SYNC=false` is opt-in per area. Full sync also carried gravity data
  (block/allow lists, groups, clients). Re-enable what you rely on:
  `SYNC_GRAVITY_GROUP`, `SYNC_GRAVITY_AD_LIST`, `SYNC_GRAVITY_DOMAIN_LIST`,
  `SYNC_GRAVITY_CLIENT`, and `SYNC_CONFIG_DHCP` if Pi-hole runs DHCP.
- Do **not** enable `SYNC_CONFIG_WEBSERVER` — `webserver.api.password` is env-pinned
  on the replicas; syncing it would 400 and (if unpinned) clobber the replica password.
- **After editing nebula-sync env you must recreate the container** — it reads env at
  start only. An error citing a key you already excluded means the old env is still live.

## Find env-pinned keys on a replica

`FTLCONF_*` vars vary per deployment. List exactly which config items are env-locked
(non-destructive) and mirror them into the EXCLUDE list:

```bash
SID=$(curl -sk -X POST https://10.1.55.10/api/auth \
  -H 'Content-Type: application/json' --data '{"password":"<pw>"}' \
  | python3 -c 'import sys,json;print(json.load(sys.stdin)["session"]["sid"])')
curl -sk "https://10.1.55.10/api/config?detailed=true" -H "X-FTL-SID: $SID" \
  | python3 -c '
import sys,json
def walk(o,p=""):
    if isinstance(o,dict):
        if "flags" in o and "value" in o and isinstance(o.get("flags"),dict):
            if o["flags"].get("env_var"): print(p)
            return
        for k,v in o.items(): walk(v,f"{p}.{k}" if p else k)
walk(json.load(sys.stdin)["config"])'
curl -sk -X DELETE https://10.1.55.10/api/auth -H "X-FTL-SID: $SID" >/dev/null
```

Anything printed under `dns.` must appear (minus the `dns.` prefix) in
`SYNC_CONFIG_DNS_EXCLUDE`.

## Troubleshoot a failed sync

nebula-sync logs only the status code, e.g.:
```
FTL Sync failed error="sync configs: https://10.1.55.10/api/config: unexpected status code: 400"
```
The useful detail is in the response body Pi-hole returns. Reproduce the PATCH to read
the `hint` (the offending key):

```bash
# GET primary config, PATCH the dns section (minus current excludes) to the replica.
# A 400 is validated before apply, so it changes nothing; a 200 WOULD apply the sync.
# hint=<key> → add that key to SYNC_CONFIG_DNS_EXCLUDE.
```

Common causes, in order of likelihood:
1. **`400 bad_request … "set via environment variables"`** → an env-pinned key is in
   the payload. Add it to the section EXCLUDE. Repeat until clean (it reports one at a time).
2. **Version skew** → `GET /api/info/version` on all three; if FTL versions differ,
   update the lagging node (`docker compose pull && docker compose up -d`).
3. **Auth failure to one replica** → a rotated replica password; update `REPLICAS`.

## Trigger and verify

```bash
# Trigger: recreate nebula-sync (or wait for its interval / run its one-shot).
# Verify the record landed on every node — primary AND both replicas must answer:
for ip in 10.1.55.9 10.1.55.10 10.1.55.11; do
  echo "$ip -> $(dig +short <fqdn> @$ip)"
done
```
All three returning the same address = healthy. Primary-only = sync still broken.

## Keeping current

nebula-sync image: `ghcr.io/lovelaze/nebula-sync`. Pin it to an explicit tag alongside
the Pi-hole version pins and update the whole DNS tier together:

```bash
docker compose pull && docker compose up -d   # in each node's compose dir, primary first
```

After any Pi-hole version bump, re-run "find env-pinned keys" — new releases add/rename
config keys, which can change what the EXCLUDE list needs.
