# silent-app

A tiny offline application used to test readiness timeouts. The process starts
and stays alive but never serves the documented health endpoint.

## Run

```bash
python -m silent
```

## Verify

```bash
curl http://localhost:8125/health
```
