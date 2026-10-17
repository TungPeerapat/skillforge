# missing-env-app

A tiny offline application that refuses to start when a required environment
variable is missing. SkillForge must diagnose this and stop instead of guessing
a value.

## Run

```bash
python -m app
```

## Verify

```bash
curl http://localhost:8126/health
```
