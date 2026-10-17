# learnable-app

A tiny, offline HTTP application used to exercise the SkillForge learning
workflow end to end. It has no third-party dependencies: only the Python
standard library.

## Requirements

- Python 3.12 or newer

## Run

The application is started by the process defined in `Procfile`:

```bash
python -m app
```

It listens on `http://localhost:8123`.

## Verify

```bash
curl http://localhost:8123/health
```

A healthy response is `200 OK` with `{"status": "ok"}`.

## Stop

Stop the process with `Ctrl+C`.
