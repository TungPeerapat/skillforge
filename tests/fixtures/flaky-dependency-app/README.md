# flaky-dependency-app

A tiny offline application whose start-up depends on a "database" that is not
immediately available. The first start fails with `connection refused`; once
`state/dependency.ready` exists it starts normally.

## Run

```bash
python -m app
```

It listens on `http://localhost:8124` once the dependency is ready.

## Verify

```bash
curl http://localhost:8124/health
```
