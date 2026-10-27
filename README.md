# Kinwatch

**A family member with eyes that lives in your WhatsApp.** Tell it what to watch in your own words or a voice note, see exactly what it understood, and when something happens, your family responds together.

Built for the Meta Global AI Developer Hackathon (Indie track, "Build for Your World"), 27 Oct – 4 Nov 2026, using Meta's Muse Spark, SAM 3.1 and Muse Voice Transcribe.

> **Status: in development.** The full README (how it works, architecture, cost, privacy) arrives before submission. See [PLAN.md](PLAN.md) for the build plan.

Kinwatch reports only what the camera saw and when. It never gives medical advice or interprets health.

## Run the tests

```bash
python -m venv .venv
.venv/Scripts/activate        # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements.txt
pytest
```

Tests use mock adapters and block all network access, so they never spend API credits or send messages.

## Run the app (mocks, no API calls)

```bash
cp .env.example .env          # USE_MOCKS=true by default
uvicorn backend.main:app
```

`GET /healthz` reports the mode; `GET /api/budget?t=<BTS_TOKEN>` shows API usage and caps.

## Run it live on your laptop

Needs [cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/)
(`winget install Cloudflare.cloudflared`) for a free HTTPS tunnel.

```bash
python scripts/dev_up.py
```

It starts the app and a Cloudflare quick tunnel, prints the camera link to open on the phone that
will act as the camera, and with `MESSENGER=whatsapp` points the WhatsApp webhook at the new tunnel
URL. Set `USE_MOCKS=false` and `META_API_KEY` to use the real Meta models.

## Check your Meta API key (about $0.02)

```bash
python scripts/live_check.py --spend --voice note.ogg --image photo.jpg --zone bed
```

One real call each to Muse Voice Transcribe, Muse Spark and SAM 3.1, counted by the budget guard.
Responses are saved under `fixtures/recorded/` with family names replaced by `{MEMBER:n}`.

## License

MIT, see [LICENSE](LICENSE).
