# flight-bot

Startup flight price tracker using Amadeus API.

## Setup

1. Register at https://developers.amadeus.com (free)
2. Copy `.env.example` to `.env` and fill in credentials
3. `test -d .venv || python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
4. `python main.py` to test manually

## Systemd service

Enable with: `systemctl --user enable flight-bot.service`
Runs automatically on login after network is up.
Logs: `journalctl --user -u flight-bot.service`

## Routes

Configured in `config/routes.yaml`. Date window and alert threshold are in the `search:` section.

## Output

- `data/prices.csv` — full price history
- `output/latest.txt` — last run summary
