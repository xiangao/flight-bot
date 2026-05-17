# flight-bot

Startup flight price tracker using Ignav or SerpAPI.

## Setup

1. Register at https://ignav.com/ for an Ignav API key
2. Copy `.env.example` to `.env` and fill in credentials
3. `test -d .venv || python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
4. `python main.py` to test manually

## Systemd service

Enable with: `systemctl --user enable flight-bot.service`
Runs automatically on login after network is up.
Logs: `journalctl --user -u flight-bot.service`

## Routes

Configured in `config/routes.yaml`. Date window and alert threshold are in the `search:` section.
Set `search.provider` to `ignav` or `serpapi`. Ignav supports round-trip
searches directly; multi-city routes are priced as separate one-way legs and summed.

## Output

- `data/prices.csv` — full price history
- `output/latest.txt` — last run summary
