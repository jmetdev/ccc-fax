# Runbook: Webex CMRG FreeSWITCH Registration

This runbook captures the working FreeSWITCH/Sofia settings for registering a Webex Calling Customer Managed Registration Gateway.

## Known Hurdle

Webex requires DNS SRV for SIP registration. FreeSWITCH/Sofia can still fail if NAPTR lookup is left enabled.

Typical failure:

```text
sofia_reg.c:463 Registering webex
sofia_reg.c:2661 webex Failed Registration with status DNS Error [503]
sofia_reg.c:520 webex Failed Registration [503], setting retry to 30 seconds.
```

This does not necessarily mean Webex DNS SRV is broken. It can mean Sofia is trying the wrong DNS lookup path before SRV.

## Working Profile Settings

In `freeswitch-docker/configuration/sip_profiles/ccc-fax.xml`:

```xml
<param name="register-transport" value="tls"/>
<param name="reuse-connections" value="true"/>
<param name="enable-rfc-5626" value="true"/>
<param name="disable-srv" value="false"/>
<param name="disable-naptr" value="true"/>
<param name="tls" value="true"/>
<param name="tls-only" value="false"/>
<param name="tls-bind-params" value="transport=tls"/>
<param name="tls-cert-dir" value="/etc/freeswitch/certs"/>
<param name="tls-verify-policy" value="none"/>
<param name="tls-verify-date" value="true"/>
```

The critical pair is:

```xml
<param name="disable-srv" value="false"/>
<param name="disable-naptr" value="true"/>
```

## Gateway Field Mapping

Webex CSV fields map to FreeSWITCH like this:

| Webex CSV Field | FreeSWITCH Gateway Param |
| --- | --- |
| Line ID user part, before `@` | `username`, `from-user`, `extension` |
| SIP Username | `auth-username` |
| SIP Password | `password` |
| Outbound Proxy | `proxy`, `register-proxy`, `outbound-proxy` |
| Line ID domain part, after `@` | `from-domain` |

Set the auth realm to:

```xml
<param name="realm" value="BroadWorks"/>
```

Webex challenges registration with the `BroadWorks` realm. If the gateway realm is set only to the Line ID domain, FreeSWITCH may log:

```text
Cannot locate any authentication credentials to complete an authentication request for realm '"BroadWorks"'
```

## Gateway Example

```xml
<gateway name="webex">
  <param name="username" value="LINE_ID_USER_PART"/>
  <param name="auth-username" value="WEBEX_SIP_USERNAME"/>
  <param name="password" value="WEBEX_SIP_PASSWORD"/>
  <param name="realm" value="BroadWorks"/>
  <param name="from-user" value="LINE_ID_USER_PART"/>
  <param name="from-domain" value="LINE_ID_DOMAIN_PART"/>
  <param name="proxy" value="WEBEX_OUTBOUND_PROXY_FQDN"/>
  <param name="register-proxy" value="WEBEX_OUTBOUND_PROXY_FQDN"/>
  <param name="outbound-proxy" value="WEBEX_OUTBOUND_PROXY_FQDN"/>
  <param name="register" value="true"/>
  <param name="register-transport" value="tls"/>
  <param name="expire-seconds" value="120"/>
  <param name="retry-seconds" value="30"/>
  <param name="timeout-seconds" value="20"/>
  <param name="caller-id-in-from" value="true"/>
  <param name="extension" value="LINE_ID_USER_PART"/>
  <param name="extension-in-contact" value="true"/>
  <param name="rfc-5626" value="true"/>
  <param name="reg-id" value="1"/>
</gateway>
```

Do not pin the proxy to an A record. Use the Webex outbound proxy FQDN so Sofia can resolve SRV targets.

## Verify DNS SRV

From the host:

```bash
dig +short SRV _sips._tcp.<webex-outbound-proxy-fqdn>
```

From FreeSWITCH:

```bash
docker exec ccc-freeswitch fs_cli -x "sofia_dig --tls <webex-outbound-proxy-fqdn>"
```

Expected: TLS SRV targets on port `8934`.

## Reload And Check Registration

```bash
docker exec ccc-freeswitch fs_cli -x reloadxml
docker exec ccc-freeswitch fs_cli -x "sofia profile ccc-fax restart reloadxml"
docker exec ccc-freeswitch fs_cli -x "sofia status gateway webex"
```

Healthy state:

```text
State    REGED
Status   UP
```

If this is set higher, Webex may request a shorter expiry:

```text
Changing expire time to 120 by request of proxy sip:<webex-outbound-proxy-fqdn>
```

That is normal.

Use `120` as the default to match Webex's requested registration interval.

## Troubleshooting Clues

DNS/NAPTR issue:

```text
Failed Registration with status DNS Error [503]
```

Fix:

```xml
<param name="disable-srv" value="false"/>
<param name="disable-naptr" value="true"/>
```

Wrong auth realm:

```text
Cannot locate any authentication credentials to complete an authentication request for realm '"BroadWorks"'
```

Fix:

```xml
<param name="realm" value="BroadWorks"/>
```

Registration timeout:

```text
Timeout Registering webex
Failed Registration [908]
```

Check TLS reachability and SRV results:

```bash
docker exec ccc-freeswitch sh -lc "nc -vz -w 5 <srv-target-host> 8934"
```

## Outbound Fax Dialing

Send PSTN destinations to Webex in E.164 format.

For example:

```text
+14808885064
```

not:

```text
4808885064
```

In testing, dialing the raw 10-digit number produced:

```text
terminated][488]
Hangup ... [INCOMPATIBLE_DESTINATION]
Originate Resulted in Error Cause: 88 [INCOMPATIBLE_DESTINATION]
```

Dialing the same destination as `+14808885064` was accepted by Webex and rang until FreeSWITCH's originate timeout:

```text
Hangup sofia/ccc-fax/+14808885064 ... [NO_ANSWER]
```

The Flask app normalizes common US 10/11 digit numbers to E.164 before queuing the originate command.

With `caller-id-in-from=true`, the originate caller ID can become the SIP From user. For Webex CMRG, keep the gateway SIP From user aligned with the registered Line ID user part, and do not let the originate caller ID replace it.

```xml
<param name="from-user" value="LINE_ID_USER_PART"/>
<param name="from-domain" value="LINE_ID_DOMAIN_PART"/>
<param name="caller-id-in-from" value="false"/>
```

Use the assigned DID separately as the originate/presentation caller ID:

```bash
FREESWITCH_CALLER_ID_NUMBER=+14804720245
FREESWITCH_OUTBOUND_FROM_USER=
```

Enable SRTP on outbound originates. Webex returned immediate `488 Not acceptable here` to plain-RTP outbound INVITEs in testing. With SRTP enabled, the call proceeded until originate timeout instead of immediate rejection.

```text
rtp_secure_media=true
sip_cid_type=pid
```

Webex/BroadWorks also needed the destination Request-URI marked as a phone number for the PSTN leg to ring:

```text
sip_invite_params=user=phone
```

Without `user=phone`, Webex accepted the authenticated INVITE with `100 Trying`, but never sent `180 Ringing` or completed the PSTN leg. With `user=phone`, Webex sent `180 Ringing`, then `200 OK`, and the destination phone rang.

## Outbound Fax TIFF Format

Outbound fax to `4809008180` was validated on 2026-04-27 through Webex CMRG with these results:

```text
Fax successfully sent.
Pages transferred: 1
Transfer Rate:     14400
T38 status         negotiated
```

The first attempt reached the remote fax endpoint and negotiated T.38, but failed because the test TIFF did not include usable fax resolution metadata:

```text
Fax processing not successful - result (11) Far end cannot receive at the resolution of the image.
Image resolution:  0x0
```

Normalize outbound documents to a fax-safe TIFF before calling `txfax`:

```text
Geometry: 1728 pixels wide
Resolution: 204x196 PixelsPerInch
Type: 1-bit bilevel
Compression: Fax / Group 3
```

Validated outbound fax signaling on 2026-04-25:

- Plain RTP outbound INVITE: immediate `488 Not acceptable here`
- SRTP outbound INVITE: Webex accepted the call
- REST fax job with SRTP: Webex returned `200 OK`, negotiated T.38, and FreeSWITCH ran `txfax`
- Transfer still failed when the remote side sent BYE immediately:

```text
T38 status         negotiated
Fax processing not successful - result (49) The call dropped prematurely.
Pages transferred: 0
```

This means the next troubleshooting step is the remote fax endpoint/session behavior, not basic Webex registration or the FreeSWITCH fax application loading.

## Notes

- Keep SIP trace off unless actively troubleshooting, because registration traces can expose credentials.
- `sofia_dig --tls` proving SRV works does not guarantee registration will work if NAPTR remains enabled.
- The Webex Outbound Proxy from Control Hub is the network target. The Line ID domain is identity-related and should be used for `from-domain`.

## Inbound DID Routing

Webex may deliver inbound calls to the registered Line ID user rather than the public DID. In FreeSWITCH logs this can look like:

```text
Processing <caller>->LINE_ID_USER_PART in context ccc-fax-inbound
Regex (FAIL) destination_number(LINE_ID_USER_PART) =~ /^\+?(\d+)$/
No Route, Aborting
Hangup ... [NO_ROUTE_DESTINATION]
```

For the first fax receive route, match both:

- The public DID, for example `4804720245`, `14804720245`, or `+14804720245`
- The Webex Line ID user part, for example `gd0fsu26lg_A1B2CDEF0012`

The current inbound dialplan saves received faxes here inside the container:

```text
/var/lib/ccc-fax/faxes/incoming
```

That path is mounted to the host workspace:

```text
/Users/jmetcalf/ccc-fax/storage/faxes/incoming
```

Use this to watch a test receive:

```bash
tail -f freeswitch-docker/logs/freeswitch.log
find storage/faxes/incoming -type f -maxdepth 1
```

## Inbound Fax Result 41

`mod_spandsp_fax` result `41` means FreeSWITCH reached `rxfax`, but could not open the destination TIFF/F file:

```text
Fax processing not successful - result (41) TIFF/F file cannot be opened.
```

On the bare-metal development server this was caused by the receive path not existing:

```text
/var/lib/ccc-fax/faxes/incoming
```

Fix the path and ownership for the FreeSWITCH service user:

```bash
mkdir -p /var/lib/ccc-fax/faxes/incoming
chown -R freeswitch:freeswitch /var/lib/ccc-fax
chmod 775 /var/lib/ccc-fax /var/lib/ccc-fax/faxes /var/lib/ccc-fax/faxes/incoming
sudo -u freeswitch sh -lc 'touch /var/lib/ccc-fax/faxes/incoming/.write-test && rm /var/lib/ccc-fax/faxes/incoming/.write-test'
```

After this fix, the next inbound test from Webex created a valid received fax:

```text
/var/lib/ccc-fax/faxes/incoming/20260427-154416-f076f45f-74df-42d8-bed5-d95ffa23207c-+17242017741-gd0fsu26lg_A1B2CDEF0012.tiff
```

The artifact was a two-page Group 3 TIFF at standard 1728-pixel fax width.

On the bare-metal install, also verify the active logfile path. The FreeSWITCH global `log_dir` was:

```text
/usr/local/freeswitch/var/log/freeswitch
```

but `logfile.conf.xml` was pointing at `/var/log/freeswitch/freeswitch.log`, which did not exist. Set the logfile entry to:

```xml
<param name="logfile" value="$${log_dir}/freeswitch.log"/>
```

Then reload and test logging:

```bash
/usr/local/freeswitch/bin/fs_cli -x 'reloadxml'
/usr/local/freeswitch/bin/fs_cli -x 'reload mod_logfile'
/usr/local/freeswitch/bin/fs_cli -x 'log err ccc-fax-logfile-test'
grep ccc-fax-logfile-test /usr/local/freeswitch/var/log/freeswitch/freeswitch.log
```
