```
              FastF1 | API Based
                      │
             Primary data ingestion
                      │
                      ▼
               Pandas / Polars
                      │
                      ▼
                 PostgreSQL
                      │
                      ▼
             Your Analysis Engine
                      │
          ┌───────────┼───────────┐
          ▼           ▼           ▼
        Pace       Strategy    Telemetry
          │           │           │
          └───────────┼───────────┘
                      ▼
                    FastAPI
                      │
                      ▼
                  Next.js UI
```