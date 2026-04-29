# CCC Fax

Flask-based administrative dashboard for Webex Calling fax workflows through FreeSWITCH.

## Current Capabilities

- Provider provisioning for Webex Calling line ID to DID mappings.
- FreeSWITCH Sofia gateway and inbound dialplan generation.
- Dashboard cards for system status, queue counts, Webex API status, gateway status, config overview, and license overview.
- Destination configuration for local inbox, SMTP relay, Webex bot, Teams webhook, and generic webhook routing.
- Outbound fax submission with DID selection and document upload.
- Document normalization to fax-compatible TIFF through ImageMagick.
- Office/OpenDocument conversion through headless LibreOffice before TIFF normalization.

## Local Setup

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` for the local FreeSWITCH config paths, Event Socket password, Webex token, and caller ID settings.

Run the app:

```bash
.venv/bin/flask --app fax_server.app:create_app run --host 0.0.0.0 --port 5000
```

## Conversion Dependencies

Install ImageMagick for PDF/image to TIFF conversion and LibreOffice for Office/OpenDocument input:

```bash
apt update
apt install -y imagemagick libreoffice-writer libreoffice-calc libreoffice-impress libreoffice-core libreoffice-common fonts-dejavu fonts-liberation
```

## FreeSWITCH Notes

Generated gateway files are written to `FREESWITCH_PROVIDER_DIR`. For a local source build, this is commonly:

```text
/usr/local/freeswitch/etc/freeswitch/sip_profiles/provider
```

The app can run `reloadxml` and `sofia profile <profile> rescan` after provisioning when `FREESWITCH_RELOAD_ON_PROVISION=true`.

Do not commit real Webex SIP credentials, generated gateway XML, certificates, logs, uploaded documents, or `.env`.
