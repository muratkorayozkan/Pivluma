# Pivluma cloud analyst gateway

Publish this container behind an HTTPS reverse proxy on a host you control. The release does not include a shared hosted endpoint.

- Build with `docker build -t pivluma-cloud server`.
- Set `OPENAI_API_KEY` in the host's secret configuration. This is the OpenAI project secret key.
- Generate a unique high-entropy access code for each approved user. Use `python -c "import secrets; print(secrets.token_urlsafe(32))"` privately. Compute its SHA-256 digest locally, and supply the comma-separated lowercase digests in `PIVLUMA_ACCESS_SHA256`. Deliver each access code privately to its user. Removing a digest revokes that code.
- Deploy one gateway instance with persistent storage mounted at `/data`, writable by UID 65532. Use HTTPS for all desktop traffic. `GET /health` reports whether credentials and access hashes are configured.
- Set `PIVLUMA_DAILY_PER_USER` and `PIVLUMA_DAILY_TOTAL` for UTC-day request limits. Defaults are 30 and 1000. Requests count against the limit once accepted, including upstream failures. `PIVLUMA_OPENAI_MODEL` defaults to `gpt-5-mini`.
- Share the HTTPS gateway address with authorized users. In **Settings**, they choose **Cloud analyst** and enter the address and issued access code.

With the secret configured in the runtime environment, run `python -m server.smoke` from the repository root to verify a real OpenAI response using synthetic data. The command prints only the outcome or HTTP status.

The gateway sends only the question and calculated facts from the desktop request to OpenAI, with response storage disabled. The app does not send price CSV files, provider keys, or credentials for the user's machine. Do not log authorization headers or request bodies at the reverse proxy.
