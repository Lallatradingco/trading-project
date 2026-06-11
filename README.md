# Trendlyne session extraction

A small utility to log in to [Trendlyne](https://trendlyne.com) and pull out an
authenticated session (the `sessionid` + `csrftoken` cookies) so it can be
reused for subsequent API/data requests without logging in each time.

## How it works

Trendlyne is a Django application, so login is a two-step flow:

1. `GET /login/` to obtain a `csrftoken` cookie and the hidden
   `csrfmiddlewaretoken` form field.
2. `POST /login/` with the credentials and the CSRF token. A successful login
   returns a `sessionid` cookie.

The script captures those cookies, optionally verifies them against an
authenticated page, and writes them to `trendlyne_session.json`.

## Usage

```bash
pip install -r requirements.txt

export TRENDLYNE_EMAIL="you@example.com"
export TRENDLYNE_PASSWORD="your-password"

# Save the session to ./trendlyne_session.json
python trendlyne_session.py

# Or just print the Cookie header (e.g. to pipe into curl)
python trendlyne_session.py --print-cookie
```

### Reusing a saved session

```python
from trendlyne_session import TrendlyneSession
import requests

sess = TrendlyneSession.load("trendlyne_session.json")
resp = requests.get(
    "https://trendlyne.com/portfolio/",
    headers={"Cookie": sess.to_cookie_header()},
)
```

## Security notes

- **Credentials** are read from environment variables and never written to disk.
- **`trendlyne_session.json`** holds live auth cookies. It is git-ignored, saved
  with `0600` permissions, and should be treated like a password.
- Use only with an account you own and in line with Trendlyne's terms of use.

## Notes on robustness

The login endpoint and form field names reflect Trendlyne's current Django
flow. If Trendlyne changes its login form, the CSRF/`sessionid` extraction may
need updating — the script raises a clear `TrendlyneAuthError` describing which
step failed.
