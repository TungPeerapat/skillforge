# Next.js Demo App

## Setup

```bash
pnpm install
cp .env.example .env
docker compose up -d db
pnpm run db:migrate
```

## Development

```bash
pnpm run dev
```

## Checks

```bash
pnpm run lint
pnpm run typecheck
pnpm test
pnpm run build
```
