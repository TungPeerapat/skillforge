# Mixed Monorepo

- `services/api` — FastAPI backend
- `services/web` — Next.js frontend
- `packages/shared` — shared TypeScript types

## Development

```bash
docker compose up -d db
make install
make test
```
