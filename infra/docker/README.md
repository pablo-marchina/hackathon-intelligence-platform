# Local infrastructure

Services:
- PostgreSQL 16 with pgvector
- Redis
- MinIO + automatic `hackathon-raw` bucket bootstrap

Start with:

```bash
docker compose -f infra/docker/compose.yml up -d
```
