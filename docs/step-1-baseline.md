# Step 1: Baseline Fax Core

This workspace now has two pieces:

- `fax_server/`: a Flask app with REST endpoints, SQLite job storage, document-to-TIFF conversion, and FreeSWITCH event socket commands.
- `freeswitch-docker/configuration/`: a minimal explicit FreeSWITCH configuration for SIP, event socket, and fax apps.

## Important Mount Note

The running Docker container named `ccc-freeswitch` is currently mounted from:

`/Users/jmetcalf/freeswitch-container/freeswitch-docker/configuration`

This workspace is:

`/Users/jmetcalf/ccc-fax/freeswitch-docker/configuration`

So this workspace's FreeSWITCH XML is a clean source scaffold, but the running container will not use it until the mount path is corrected or the files are copied into the active mount.

## FreeSWITCH Shape

- `autoload_configs/modules.conf.xml` loads the core modules needed for SIP and fax.
- `autoload_configs/event_socket.conf.xml` exposes ESL on `127.0.0.1:8021`.
- `autoload_configs/sofia.conf.xml` loads SIP profiles.
- `sip_profiles/ccc-fax.xml` defines a dedicated SIP profile on port `5070`.
- `sip_profiles/provider/webex.xml` is the active Webex CMRG gateway.
- `docs/webex.gateway.example.xml` is a non-loaded template.
- `dialplan/ccc-fax.xml` is the first inbound fax receiver context.

## Flask API

- `GET /api/health`
- `GET /api/faxes`
- `POST /api/faxes` with multipart fields `to_number` and `document`
- `GET /api/faxes/<id>`
- `POST /api/faxes/<id>/send`
- `GET /api/inbound-routes`
- `POST /api/inbound-routes` with `webex_line_id`, `did_number`, optional `extension`, and optional delivery fields
- `GET /api/inbound-routes/<id>`
- `PUT /api/inbound-routes/<id>`
- `DELETE /api/inbound-routes/<id>`
- `POST /api/webex/provision-gateway` with a customer-managed gateway `mac`
- `POST /api/webex/provision` with a Control Hub `workspace_url` and `gateway_url` for single-line discovery
- `POST /api/inbound` for a FreeSWITCH hook after inbound fax receive

## Inbound Routing Model

Inbound line mappings live in the `inbound_routes` SQLite table. Each route correlates the Webex `line_id`/`lineID` that FreeSWITCH sees with a fax-server DID and optional extension:

- `webex_line_id`: Webex line identifier, unique.
- `webex_workspace_id`: source Webex workspace ID, when provisioned from Webex.
- `webex_gateway_id`: source Webex customer-managed gateway/device ID, when provisioned from Webex.
- `did_number`: public DID for the fax line, stored normalized as E.164 when possible.
- `extension`: optional internal extension, unique when present.
- `display_name`: operator-facing route label.
- `destination_type`: `local`, `email`, or `webhook`; only `local` storage is implemented today.
- `destination_value`: reserved for the email address or webhook URL when those delivery paths are added.

Inbound fax jobs now store `webex_line_id` and `inbound_route_id`. If `/api/inbound` receives a known `lineID`, or if an inbound FreeSWITCH `rxfax` result arrives with a destination number matching a route, the job is marked `received` and linked to the route. If no route matches, the job is marked `unrouted` so it is visible in the console.

## Webex Provisioning

Set `WEBEX_ACCESS_TOKEN` to an administrator token that can read Devices and Telephony Config. For partner/customer contexts, set `WEBEX_ORG_ID` or pass `org_id` to the provisioning endpoint.

The primary provisioning endpoint accepts the customer-managed gateway MAC address:

```json
{
  "mac": "A1B2CDEF0012"
}
```

It calls Webex in this order:

1. `GET /v1/devices?mac=A1B2CDEF0012`
2. `GET /v1/telephony/config/devices/{deviceId}/members`

Each member with a `lineId` and phone number is upserted into `inbound_routes`. The app then rewrites `FREESWITCH_INBOUND_DIALPLAN` and creates one generated Sofia gateway file per line in `FREESWITCH_PROVIDER_DIR`. Generated gateway files reuse the credentials and proxy settings from `FREESWITCH_PARENT_GATEWAY` but replace `username`, `from-user`, and `extension` with the member `lineId`.

The generated files are named `generated-webex-line-*.xml`, so they are loaded by the existing `provider/*.xml` include. The app runs `reloadxml` when `FREESWITCH_RELOAD_ON_PROVISION=true`.

For local Mac/Docker development the app tries FreeSWITCH ESL first, then falls back to:

```bash
docker exec ccc-freeswitch fs_cli -x "<command>"
```

That fallback is controlled by `FREESWITCH_CLI_CONTAINER`.

## Run Locally

```bash
cp .env.example .env
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
flask --app fax_server.app:create_app run
```

Then open `http://127.0.0.1:5000`.

## Next Step

Fill in `freeswitch-docker/configuration/sip_profiles/provider/webex.xml`, then reload FreeSWITCH:

```bash
docker exec ccc-freeswitch fs_cli -x reloadxml
docker exec ccc-freeswitch fs_cli -x "load mod_sofia"
docker exec ccc-freeswitch fs_cli -x "sofia status"
```

The send endpoint now checks `module_exists mod_sofia` before originating so a missing SIP profile shows up as a clear API error.

For the Webex/Sofia DNS SRV registration details, see `docs/runbook-webex-freeswitch-registration.md`.
