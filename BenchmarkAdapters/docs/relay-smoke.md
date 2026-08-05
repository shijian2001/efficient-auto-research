# Relay Smoke Record

- Endpoint: `https://relay.shuai-ederson-clow.xyz/v1`
- Model: `gpt-5.5`
- Proxy: `http://127.0.0.1:17892`

The local proxy accepted HTTP CONNECT and SOCKS5 traffic and reached GitHub.
The relay hostname resolved, but its TLS handshake timed out through port
`17892` during the current smoke test. `/v1/models` therefore returned no
payload. This is a relay transport issue, not an adapter parsing failure.

