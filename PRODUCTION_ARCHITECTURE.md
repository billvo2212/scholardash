# Production Architecture for ScholarHub at Scale

**Scenario:** 10,000+ concurrent users globally accessing ScholarHub data product

**Requirements:**
- Global availability (multi-region)
- Sub-second query response times
- 99.9% uptime SLA
- Handle 10M+ grants (not just 1,000)
- Cost-optimized at scale

---

## Current Architecture (Portfolio/MVP)

```
┌──────────────────────────────────────────────────────┐
│  Local Machine / Single Server                       │
│  ┌────────────────┐  ┌──────────────────────────┐   │
│  │ Streamlit App  │  │ Airflow (Docker Compose) │   │
│  └────────┬───────┘  └──────────┬───────────────┘   │
│           │                     │                     │
│           └─────────┬───────────┘                     │
│                     ↓                                 │
│           ┌──────────────────┐                        │
│           │ DuckDB File      │                        │
│           │ (100 MB)         │                        │
│           └──────────────────┘                        │
└──────────────────────────────────────────────────────┘
```

**Limitations:**
- ❌ Single point of failure
- ❌ Can't handle 10,000 concurrent users
- ❌ No geographic distribution
- ❌ DuckDB file locks under high concurrency
- ❌ No horizontal scaling
- ❌ No caching layer

---

## Production Architecture (10K+ Users)

```
┌─────────────────────────────────────────────────────────────────────────┐
│                           USER LAYER (Global)                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │  US East    │  │  EU West    │  │  Asia Pac   │  │  US West    │   │
│  │  Users      │  │  Users      │  │  Users      │  │  Users      │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         │                │                │                │            │
└─────────┼────────────────┼────────────────┼────────────────┼────────────┘
          │                │                │                │
          └────────────────┴────────────────┴────────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                        EDGE/CDN LAYER (CloudFlare)                       │
│  - Global content delivery                                              │
│  - DDoS protection                                                       │
│  - Static asset caching (60-90% of requests never hit backend)          │
│  - Web Application Firewall (WAF)                                       │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                    APPLICATION LAYER (Multi-Region)                      │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Global Load Balancer (AWS Route 53 / GCP GLB)                     │ │
│  │  - Geo-routing: Users → nearest region                             │ │
│  │  - Health checks & failover                                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐         │
│  │  US Region      │  │  EU Region      │  │  APAC Region    │         │
│  │  ┌───────────┐  │  │  ┌───────────┐  │  │  ┌───────────┐  │         │
│  │  │ ALB/LB    │  │  │  │ ALB/LB    │  │  │  │ ALB/LB    │  │         │
│  │  └─────┬─────┘  │  │  └─────┬─────┘  │  │  └─────┬─────┘  │         │
│  │        │        │  │        │        │  │        │        │         │
│  │  ┌─────▼──────┐ │  │  ┌─────▼──────┐ │  │  ┌─────▼──────┐ │         │
│  │  │ ECS/K8s    │ │  │  │ ECS/K8s    │ │  │  │ ECS/K8s    │ │         │
│  │  │ Auto-Scale │ │  │  │ Auto-Scale │ │  │  │ Auto-Scale │ │         │
│  │  │ 2-20 pods  │ │  │  │ 2-20 pods  │ │  │  │ 2-20 pods  │ │         │
│  │  └────────────┘ │  │  └────────────┘ │  │  └────────────┘ │         │
│  └─────────────────┘  └─────────────────┘  └─────────────────┘         │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                         API / BACKEND LAYER                              │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  FastAPI / Flask REST API (Containerized)                          │ │
│  │  - /api/v1/awards?filters=...                                      │ │
│  │  - /api/v1/institutions                                            │ │
│  │  - /api/v1/trends                                                  │ │
│  │  - Rate limiting: 100 req/min per user                             │ │
│  │  - Authentication: OAuth2 / JWT                                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                          CACHING LAYER                                   │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Redis Cluster (ElastiCache / Memorystore)                         │ │
│  │  - Query result caching (TTL: 1-24 hours)                          │ │
│  │  - 95%+ cache hit rate for common queries                          │ │
│  │  - Multi-region replication                                        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↓
┌─────────────────────────────────────────────────────────────────────────┐
│                        DATA WAREHOUSE LAYER                              │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Snowflake / BigQuery / Redshift (Choose ONE)                      │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  MARTS (Pre-aggregated, Query-Optimized)                     │ │ │
│  │  │  - mart_funding_by_institution (materialized view)           │ │ │
│  │  │  - mart_funding_by_field (materialized view)                 │ │ │
│  │  │  - mart_funding_by_year (materialized view)                  │ │ │
│  │  │  - mart_professor_profiles (materialized view)               │ │ │
│  │  │  → Queries: <100ms (indexed, partitioned)                    │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  INTERMEDIATE (Business Logic Layer)                         │ │ │
│  │  │  - int_all_awards (10M+ rows, partitioned by year/source)   │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  STAGING (Source-Specific, Daily Refreshed)                  │ │ │
│  │  │  - stg_nsf_awards (5M rows)                                  │ │ │
│  │  │  - stg_nih_projects (3M rows)                                │ │ │
│  │  │  - stg_nserc_awards (2M rows)                                │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  │  ┌──────────────────────────────────────────────────────────────┐ │ │
│  │  │  RAW (Immutable, Append-Only, Compressed)                    │ │ │
│  │  │  - raw_nsf_awards (Parquet, compressed, 500 GB)              │ │ │
│  │  │  - raw_nih_projects (Parquet, compressed, 300 GB)            │ │ │
│  │  └──────────────────────────────────────────────────────────────┘ │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  **Storage Optimization:**                                               │
│  - Columnar format (Parquet)                                             │
│  - Partitioned by: year, source, state                                   │
│  - Clustered by: institution, program_name                               │
│  - Compressed: ZSTD (5-10x reduction)                                    │
│  - Cost: ~$500-1,000/month for 10M records                               │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↑
┌─────────────────────────────────────────────────────────────────────────┐
│                      ORCHESTRATION LAYER                                 │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Managed Airflow (AWS MWAA / GCP Composer / Astronomer)            │ │
│  │  - Daily DAGs for each source                                      │ │
│  │  - Parallel extraction (4 sources simultaneously)                  │ │
│  │  - dbt runs after all extractions complete                         │ │
│  │  - Retry logic, alerting, lineage tracking                         │ │
│  │  - Cost: $300-500/month                                            │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  **DAG Structure:**                                                      │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐   │
│  │ extract_nsf │  │ extract_nih │  │extract_nserc│  │extract_cihr │   │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘   │
│         └─────────────────┴─────────────────┴─────────────────┘         │
│                                   ↓                                      │
│                          ┌─────────────────┐                             │
│                          │  dbt_run        │                             │
│                          │  (all models)   │                             │
│                          └────────┬────────┘                             │
│                                   ↓                                      │
│                          ┌─────────────────┐                             │
│                          │  dbt_test       │                             │
│                          └────────┬────────┘                             │
│                                   ↓                                      │
│                          ┌─────────────────┐                             │
│                          │ invalidate_cache│                             │
│                          │ (flush Redis)   │                             │
│                          └─────────────────┘                             │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↑
┌─────────────────────────────────────────────────────────────────────────┐
│                         EXTRACTION LAYER                                 │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Kubernetes Jobs / AWS Lambda / Cloud Functions                    │ │
│  │  - Serverless extractors for each API                              │ │
│  │  - Parallel execution (4+ sources)                                 │ │
│  │  - Auto-retry, rate limiting built-in                              │ │
│  │  - Write to cloud storage (S3/GCS), not directly to DB             │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  **Data Flow:**                                                          │
│  API → JSON files (S3/GCS) → dbt reads from S3 → Snowflake/BigQuery    │
└─────────────────────────────────────────────────────────────────────────┘
                                   ↑
┌─────────────────────────────────────────────────────────────────────────┐
│                    MONITORING & OBSERVABILITY                            │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │  Datadog / New Relic / Grafana + Prometheus                        │ │
│  │  - Application metrics (request latency, error rates)              │ │
│  │  - Infrastructure metrics (CPU, memory, disk)                      │ │
│  │  - Data quality metrics (freshness, completeness, accuracy)        │ │
│  │  - Custom dashboards for business KPIs                             │ │
│  │  - PagerDuty/Opsgenie for on-call alerts                           │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                           │
│  **SLIs (Service Level Indicators):**                                    │
│  - API p95 latency < 500ms                                               │
│  - API error rate < 0.1%                                                 │
│  - Dashboard page load < 2 seconds                                       │
│  - Data freshness < 24 hours                                             │
│  - Cache hit rate > 95%                                                  │
│  - Uptime > 99.9% (43 minutes downtime/month max)                        │
└─────────────────────────────────────────────────────────────────────────┘
```

---

## Key Architecture Decisions

### 1. Database: Snowflake (Recommended)

**Why not DuckDB?**
- ❌ DuckDB is single-node (no horizontal scaling)
- ❌ File-based = locks under high concurrency
- ❌ No multi-region replication

**Why Snowflake?**
- ✅ Separates compute from storage (scale independently)
- ✅ Auto-scales for concurrent queries
- ✅ Zero-copy cloning (dev/staging/prod environments)
- ✅ Time travel (query historical data)
- ✅ Built-in data sharing (multi-tenant if needed)
- ✅ Materialized views auto-refresh
- ✅ Cost: ~$1,000-2,000/month for 10M records

**Alternative:** BigQuery (similar benefits, GCP ecosystem)

---

### 2. Application Architecture: Decoupled Frontend/Backend

**Current (Monolith):**
```
Streamlit App
  ↓
Queries DuckDB directly
```

**Production (Decoupled):**
```
React Frontend (Next.js)
  ↓ (HTTPS/REST)
FastAPI Backend
  ↓ (SQL)
Snowflake/BigQuery
```

**Why decouple?**
- ✅ Frontend can be served from CDN (fast globally)
- ✅ Backend can scale independently
- ✅ API enables mobile apps, third-party integrations
- ✅ Better security (API keys, rate limiting)
- ✅ Multiple frontends (web, mobile, Slack bot)

---

### 3. Caching Strategy (Critical for Performance)

**3-Tier Caching:**

**Tier 1: CDN (CloudFlare/CloudFront)**
- Cache: Static assets (JS, CSS, images)
- TTL: 30 days
- Hit rate: 90%+ (for static content)

**Tier 2: Application Cache (Redis)**
- Cache: Query results (common queries)
- TTL: 1-24 hours (depending on query)
- Hit rate: 95%+ (for common queries)
- Example:
  ```python
  # Pseudo-code
  def get_top_institutions():
      cache_key = "top_institutions_v1"
      result = redis.get(cache_key)
      if result:
          return result  # Cache hit (< 1ms)

      # Cache miss - query Snowflake
      result = snowflake.execute("SELECT ... FROM mart_funding_by_institution LIMIT 10")
      redis.setex(cache_key, 3600, result)  # Cache for 1 hour
      return result
  ```

**Tier 3: Database Cache (Snowflake Result Cache)**
- Snowflake caches query results for 24 hours
- Free (no compute cost for cached queries)
- Automatic

**Result:** 95%+ of user requests never touch the database (sub-50ms response)

---

### 4. Global Distribution Strategy

**Problem:** Users in Asia experience 200-500ms latency to US servers

**Solution:** Multi-region deployment

```
User in Tokyo
  ↓ (5ms)
CloudFlare Edge (Tokyo)
  ↓ (10ms)
AWS APAC Region (Singapore)
  ↓ (50ms)
Snowflake Multi-Region (replicates data)
```

**Total latency:** ~65ms (vs 500ms single-region)

**Cost:** +30% for multi-region (worth it for global product)

---

### 5. Auto-Scaling Configuration

**Frontend (ECS/Kubernetes):**
```yaml
autoscaling:
  min_replicas: 2  # Always-on for HA
  max_replicas: 20
  target_cpu: 70%  # Scale up at 70% CPU
  target_memory: 80%

  # Scale up aggressively, down conservatively
  scale_up_cooldown: 60s
  scale_down_cooldown: 300s
```

**Cost:** $200-500/month (2-20 containers @ $0.05/hour each)

**Backend API (Serverless):**
- AWS Lambda or Cloud Run
- Auto-scales to 1,000+ concurrent executions
- Pay per request (no idle cost)
- Cost: $0.20 per 1M requests

**Database (Snowflake):**
```sql
-- Auto-suspend after 5 minutes of inactivity
ALTER WAREHOUSE SCHOLARHUB_WH
SET AUTO_SUSPEND = 300;

-- Auto-resume on query
SET AUTO_RESUME = TRUE;

-- Size: X-Small (1 credit/hour = $2-4/hour)
-- Scales to X-Large for heavy queries
```

---

### 6. Data Pipeline at Scale

**Current Pipeline:**
```
extract_nsf (500 records, 2 min)
  ↓
extract_nih (500 records, 2 min)
  ↓
dbt (6 models, 1 sec)
```

**Production Pipeline:**
```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│ extract_nsf     │  │ extract_nih     │  │ extract_nserc   │  │ extract_cihr    │
│ (50K/day, 10min)│  │ (30K/day, 8min) │  │ (5K/day, 5min)  │  │ (3K/day, 3min)  │
└────────┬────────┘  └────────┬────────┘  └────────┬────────┘  └────────┬────────┘
         │                    │                    │                    │
         └────────────────────┴────────────────────┴────────────────────┘
                                       ↓
                          ┌─────────────────────────┐
                          │  dbt run --models staging│
                          │  (4 models, 2 min)        │
                          └────────────┬──────────────┘
                                       ↓
                          ┌─────────────────────────┐
                          │  dbt run --models intermediate│
                          │  (1 model, 5 min)        │
                          └────────────┬──────────────┘
                                       ↓
                          ┌─────────────────────────┐
                          │  dbt run --models marts  │
                          │  (10 models, 3 min)      │
                          └────────────┬──────────────┘
                                       ↓
                          ┌─────────────────────────┐
                          │  dbt test                │
                          │  (50 tests, 1 min)       │
                          └────────────┬──────────────┘
                                       ↓
                          ┌─────────────────────────┐
                          │  invalidate_cache        │
                          │  (flush Redis, 10 sec)   │
                          └──────────────────────────┘
```

**Total Runtime:** ~30 minutes (parallel extraction = faster)

**Incremental Models:**
```sql
-- Only process new data
{{ config(
    materialized='incremental',
    unique_key='award_id',
    partition_by={'field': 'award_year', 'data_type': 'int'}
) }}

SELECT * FROM source
{% if is_incremental() %}
    WHERE extracted_at > (SELECT MAX(extracted_at) FROM {{ this }})
{% endif %}
```

---

## Cost Breakdown (10K Concurrent Users)

| Component | Service | Monthly Cost |
|-----------|---------|--------------|
| **Database** | Snowflake X-Small | $1,500 |
| **Orchestration** | AWS MWAA (Airflow) | $400 |
| **Application** | ECS Fargate (3 regions) | $600 |
| **Caching** | ElastiCache Redis | $200 |
| **CDN** | CloudFlare Pro | $20 |
| **Load Balancer** | ALB (3 regions) | $60 |
| **Monitoring** | Datadog Pro | $300 |
| **Storage** | S3 (1 TB) | $25 |
| **Data Transfer** | Egress (global) | $200 |
| **Domain/SSL** | Route 53 + ACM | $5 |
| **Misc** | Backups, logs | $100 |
| **TOTAL** | | **~$3,400/month** |

**Per User Cost:** $0.34/month (for 10K concurrent users)

**At 100K users:** ~$6,000/month ($0.06/user)

---

## Revenue Model (Optional)

To justify $3,400/month infrastructure cost:

**Freemium Model:**
- **Free Tier:** 100 queries/month, basic dashboard
- **Pro Tier:** $9.99/month - unlimited queries, API access, alerts
- **Enterprise:** $99/month - white-label, data exports, priority support

**Break-even:** 350 Pro users or 35 Enterprise customers

**Alternative:** Grant foundation sponsorship (Bill & Melinda Gates Foundation, etc.)

---

## Migration Path from Portfolio to Production

### Phase 1: Proof of Scale (Week 1-2)
- Migrate DuckDB → Snowflake (trial account, free $400 credit)
- Deploy frontend to Vercel/Netlify (free tier)
- Add Redis cache (ElastiCache free tier or Railway.app)
- Test with 100 concurrent users (load testing with Locust)

### Phase 2: Regional Deployment (Week 3-4)
- Deploy to 2 regions (US + EU)
- Add CloudFlare CDN
- Implement API layer (FastAPI)
- Set up monitoring (Datadog trial or Grafana Cloud free)

### Phase 3: Full Production (Week 5-8)
- Deploy to 3 regions (US, EU, APAC)
- Migrate Airflow to MWAA/Composer
- Implement auto-scaling
- Add authentication, rate limiting
- Security audit, penetration testing
- Go live 🚀

**Total Migration Time:** 6-8 weeks (part-time)
**Total Cost:** ~$3,500/month ongoing

---

## Security Hardening (Critical for Production)

### 1. Authentication & Authorization
```python
# JWT-based authentication
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="token")

@app.get("/api/v1/awards")
async def get_awards(token: str = Depends(oauth2_scheme)):
    # Verify token, extract user_id
    # Check user permissions
    # Return data scoped to user tier (free/pro/enterprise)
```

### 2. Rate Limiting
```python
# Per-user rate limiting
from slowapi import Limiter

limiter = Limiter(key_func=get_user_id)

@app.get("/api/v1/awards")
@limiter.limit("100/hour")  # 100 requests/hour per user
async def get_awards():
    ...
```

### 3. Data Encryption
- **In Transit:** TLS 1.3 everywhere (CloudFlare → ALB → App → Snowflake)
- **At Rest:** AES-256 encryption (Snowflake default)
- **Database:** Column-level encryption for sensitive fields (if any)

### 4. Network Security
- VPC with private subnets (app can't be accessed from internet directly)
- Security groups: Only ALB → App, App → Database
- No SSH access (use AWS Systems Manager Session Manager)

### 5. Secrets Management
- AWS Secrets Manager or HashiCorp Vault
- Rotate API keys quarterly
- Never commit secrets to git (use environment variables)

---

## Disaster Recovery & Business Continuity

### RTO (Recovery Time Objective): 15 minutes
- Multi-region deployment = automatic failover
- If US region fails → EU takes over (Route 53 health checks)

### RPO (Recovery Point Objective): 1 hour
- Snowflake automatic backups (Time Travel: 90 days)
- S3 versioning enabled (can recover deleted data)
- Database snapshots every 6 hours

### Backup Strategy
```sql
-- Snowflake zero-copy clone for backups
CREATE DATABASE scholarhub_backup
CLONE scholarhub;

-- Restore from backup
CREATE DATABASE scholarhub
CLONE scholarhub_backup AT (TIMESTAMP => '2024-03-20 10:00:00');
```

**Cost:** Near-zero (Snowflake only charges for delta storage)

---

## Monitoring & Alerting

### Critical Alerts (Page On-Call Engineer)
- API error rate > 1% for 5 minutes
- P95 latency > 2 seconds for 5 minutes
- Data pipeline failed
- Any region unhealthy

### Warning Alerts (Slack/Email)
- Cache hit rate < 90%
- Data freshness > 36 hours
- Unusual query patterns (potential abuse)
- Cost anomaly (>20% increase week-over-week)

### Dashboard KPIs
- Real-time concurrent users
- Requests per second
- Error rate by endpoint
- Query latency (p50, p95, p99)
- Infrastructure costs (daily burn rate)
- User engagement (queries per user, retention)

---

## Interview Talking Points

**If asked:** *"How would you scale ScholarHub to 10,000 users?"*

**Answer:**
> "I'd migrate from DuckDB to Snowflake for horizontal scaling, implement a three-tier caching strategy (CDN, Redis, database cache) to achieve 95%+ cache hit rates, deploy to multiple regions for global latency <100ms, and use managed Airflow (MWAA) for reliable orchestration. The key architectural change is decoupling the frontend from the database with a FastAPI layer, enabling auto-scaling and rate limiting. This brings the cost to about $0.34 per user per month, with infrastructure running around $3,500/month for 10K concurrent users."

---

## Summary: Portfolio vs Production

| Aspect | Portfolio (Current) | Production (10K Users) |
|--------|---------------------|------------------------|
| **Database** | DuckDB (100 MB file) | Snowflake (10M+ records) |
| **App Hosting** | Streamlit Cloud (free) | ECS/K8s (multi-region) |
| **Caching** | None | Redis + CDN (95% hit rate) |
| **Orchestration** | Docker Compose (local) | Managed Airflow (AWS MWAA) |
| **API** | None | FastAPI (versioned, authenticated) |
| **Monitoring** | Basic logs | Datadog + PagerDuty |
| **Regions** | Single (US) | Multi-region (US, EU, APAC) |
| **Security** | Basic | OAuth2, WAF, encryption |
| **Cost** | $0/month | $3,500/month |
| **Latency** | 1-5 seconds | 50-200ms (cached) |
| **Uptime SLA** | None | 99.9% |
| **Scalability** | 1-10 users | 10K+ concurrent |

**Your current architecture is perfect for a portfolio.** This production design shows you understand enterprise-scale data engineering. 🚀
