# Deployment Options for ScholarHub

This guide outlines practical deployment options for running ScholarHub in production (not on your local machine).

---

## Overview

ScholarHub has **two main components** to deploy:

1. **Streamlit Dashboard** (user-facing frontend)
2. **Airflow + DuckDB Pipeline** (backend data processing)

---

## Option 1: Streamlit Cloud + GitHub Actions (RECOMMENDED FOR PORTFOLIO)

### Architecture
```
┌─────────────────────────────────────────────────────┐
│  Streamlit Cloud (Free Tier)                        │
│  - Hosts dashboard                                  │
│  - Reads from DuckDB file in GitHub repo            │
│  - Public URL: https://your-app.streamlit.app      │
└─────────────────────────────────────────────────────┘
                    ↓ reads from
┌─────────────────────────────────────────────────────┐
│  GitHub Repository                                  │
│  - warehouse/scholarhub.duckdb (committed)          │
│  - Updated via GitHub Actions                       │
└─────────────────────────────────────────────────────┘
                    ↑ updated by
┌─────────────────────────────────────────────────────┐
│  GitHub Actions (Free)                              │
│  - Runs extractors on schedule (daily)              │
│  - Runs dbt transformations                         │
│  - Commits updated .duckdb file                     │
└─────────────────────────────────────────────────────┘
```

### Pros
- ✅ **100% Free**
- ✅ **Easy setup** (connect GitHub, click deploy)
- ✅ **Auto-deploys** on git push
- ✅ **HTTPS domain** included
- ✅ **Perfect for portfolio** (shareable link)

### Cons
- ⚠️ DuckDB file size limit (~100 MB for free tier)
- ⚠️ GitHub Actions: 2,000 minutes/month free (enough for daily runs)
- ⚠️ Dashboard sleeps after inactivity (wakes in ~30 sec)

### Setup Steps

**1. Push to GitHub**
```bash
git init
git add .
git commit -m "Initial commit"
git remote add origin https://github.com/yourusername/scholarhub.git
git push -u origin main
```

**2. Deploy Dashboard to Streamlit Cloud**
- Go to https://share.streamlit.io
- Click "New app"
- Connect GitHub repo
- Set main file: `dashboard/app.py`
- Click "Deploy"
- Get public URL: `https://scholarhub-yourusername.streamlit.app`

**3. Create GitHub Actions Workflow**
```yaml
# .github/workflows/daily_pipeline.yml
name: Daily Data Pipeline

on:
  schedule:
    - cron: '0 6 * * *'  # 6 AM UTC daily
  workflow_dispatch:  # Manual trigger

jobs:
  run-pipeline:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v3

      - name: Set up Python
        uses: actions/setup-python@v4
        with:
          python-version: '3.11'

      - name: Install dependencies
        run: |
          pip install duckdb requests python-dotenv pydantic-settings tenacity structlog tqdm dbt-duckdb

      - name: Run NSF Extractor
        run: python -m extractors.federal_apis.nsf_extractor

      - name: Run NIH Extractor
        run: python -m extractors.federal_apis.nih_extractor

      - name: Run dbt transformations
        run: |
          cd transform/scholarhub
          dbt run --profiles-dir .
          dbt test --profiles-dir .

      - name: Commit updated database
        run: |
          git config --global user.name 'GitHub Actions'
          git config --global user.email 'actions@github.com'
          git add warehouse/scholarhub.duckdb
          git commit -m "Update data: $(date +'%Y-%m-%d')" || echo "No changes"
          git push
```

**Cost:** $0/month
**Maintenance:** Zero (fully automated)

---

## Option 2: AWS (Professional Grade)

### Architecture
```
┌─────────────────────────────────────────────────────┐
│  AWS Elastic Beanstalk or ECS                       │
│  - Hosts Streamlit dashboard                        │
│  - Connects to RDS/S3                               │
└─────────────────────────────────────────────────────┘
                    ↓ reads from
┌─────────────────────────────────────────────────────┐
│  Amazon RDS (PostgreSQL) or S3 + DuckDB            │
│  - Stores transformed data                          │
│  - Or: DuckDB file on S3                           │
└─────────────────────────────────────────────────────┘
                    ↑ updated by
┌─────────────────────────────────────────────────────┐
│  AWS MWAA (Managed Airflow)                         │
│  - Runs DAGs on schedule                            │
│  - $300-400/month (cheapest instance)               │
└─────────────────────────────────────────────────────┘
```

### Components
- **Dashboard:** ECS Fargate or Elastic Beanstalk ($5-20/month)
- **Database:** RDS PostgreSQL t3.micro ($15/month) or S3 + DuckDB ($1/month)
- **Airflow:** MWAA ($300/month) or self-hosted EC2 ($10/month)

### Pros
- ✅ Production-grade
- ✅ Scalable
- ✅ Professional infrastructure

### Cons
- ❌ **Expensive** ($300+/month for MWAA)
- ❌ Complex setup
- ❌ Overkill for portfolio project

### Cheaper AWS Alternative
**Use EC2 + Docker instead of MWAA:**
- t3.small EC2 instance ($15/month)
- Run your existing `docker-compose.yml`
- Install Nginx for reverse proxy
- Use Elastic IP for static IP

**Cost:** ~$15-20/month

---

## Option 3: Google Cloud Platform (GCP)

### Architecture
```
┌─────────────────────────────────────────────────────┐
│  Cloud Run (Streamlit)                              │
│  - Serverless container hosting                     │
│  - Pay per request (free tier: 2M requests/month)   │
└─────────────────────────────────────────────────────┘
                    ↓ reads from
┌─────────────────────────────────────────────────────┐
│  Cloud Storage (DuckDB file) or BigQuery           │
│  - $0.02/GB/month storage                          │
└─────────────────────────────────────────────────────┘
                    ↑ updated by
┌─────────────────────────────────────────────────────┐
│  Cloud Composer (Managed Airflow)                   │
│  - $300-400/month (or Cloud Scheduler + Functions)  │
└─────────────────────────────────────────────────────┘
```

### Budget Option
**Use Cloud Scheduler + Cloud Functions instead of Composer:**
- Cloud Scheduler triggers Cloud Function daily
- Cloud Function runs extractors + dbt
- Updates DuckDB file in Cloud Storage
- Cloud Run serves dashboard

**Cost:** ~$5-10/month (mostly Cloud Run)

### Pros
- ✅ Generous free tier
- ✅ Cloud Run auto-scales to zero (pay only when used)
- ✅ BigQuery free tier: 1 TB queries/month

### Cons
- ⚠️ Cloud Composer (managed Airflow) is expensive
- ⚠️ More setup than Streamlit Cloud

---

## Option 4: Render.com (Middle Ground)

### Architecture
```
┌─────────────────────────────────────────────────────┐
│  Render Web Service (Streamlit)                     │
│  - Free tier or $7/month                            │
│  - Auto-deploy from GitHub                          │
└─────────────────────────────────────────────────────┘
                    ↓ reads from
┌─────────────────────────────────────────────────────┐
│  Render Disk Storage (DuckDB)                       │
│  - Persistent disk for free tier                    │
└─────────────────────────────────────────────────────┘
                    ↑ updated by
┌─────────────────────────────────────────────────────┐
│  Render Cron Job                                    │
│  - Runs Python script on schedule                   │
│  - $7/month per job                                 │
└─────────────────────────────────────────────────────┘
```

### Pros
- ✅ Simple Heroku-like experience
- ✅ Auto-deploy from GitHub
- ✅ Cheaper than AWS/GCP for small apps
- ✅ Free tier available

### Cons
- ⚠️ Free tier sleeps after 15 min inactivity
- ⚠️ No managed Airflow (use cron jobs)
- ⚠️ Limited to 512 MB disk on free tier

**Cost:**
- Free tier: $0/month (with sleep)
- Paid: $7-14/month

---

## Option 5: Railway.app (Developer Friendly)

### Architecture
Similar to Render, but with better developer experience.

### Pros
- ✅ $5/month credit on free tier
- ✅ Great CLI tools
- ✅ PostgreSQL included
- ✅ Easy cron jobs

### Cons
- ⚠️ Credit runs out if high usage
- ⚠️ No managed Airflow

**Cost:** Free tier → $5-10/month

---

## Recommendation by Use Case

### For Portfolio / Resume
**Best Choice:** **Streamlit Cloud + GitHub Actions**
- **Cost:** $0/month
- **Effort:** 30 minutes setup
- **Result:** Public URL to share with recruiters
- **Limitation:** Dashboard sleeps (acceptable for portfolio)

### For Side Project / Small Users
**Best Choice:** **Render.com** or **Railway.app**
- **Cost:** $7-10/month
- **Effort:** 1 hour setup
- **Result:** Always-on dashboard + automated pipeline

### For Production / Resume (Show AWS Skills)
**Best Choice:** **AWS EC2 + Docker**
- **Cost:** $15-20/month
- **Effort:** 2-3 hours setup
- **Result:** "Deployed production data pipeline on AWS" on resume
- **Setup:**
  1. Launch t3.small EC2 instance
  2. Install Docker
  3. Copy `docker-compose.yml` to instance
  4. Run `docker-compose up -d`
  5. Configure Nginx reverse proxy
  6. Point domain to Elastic IP

### For Learning Cloud Data Engineering
**Best Choice:** **GCP Cloud Run + Cloud Functions**
- **Cost:** $5-10/month
- **Effort:** 2-3 hours
- **Result:** Learn serverless, Cloud Storage, BigQuery
- **Bonus:** "Serverless data pipeline on GCP" resume bullet

---

## Migration Path: DuckDB → Cloud Database

If you deploy to production and want to scale beyond DuckDB:

### DuckDB → PostgreSQL (RDS/Cloud SQL)
```bash
# Export from DuckDB
duckdb warehouse/scholarhub.duckdb -c "COPY int_all_awards TO 'awards.csv'"

# Import to PostgreSQL
psql -h your-rds-endpoint.amazonaws.com -U postgres -c "\COPY int_all_awards FROM 'awards.csv' CSV HEADER"
```

### DuckDB → BigQuery
```python
# Use DuckDB BigQuery extension
import duckdb
conn = duckdb.connect()
conn.execute("INSTALL bigquery")
conn.execute("LOAD bigquery")
conn.execute("COPY int_all_awards TO 'bigquery://your-project/dataset/table'")
```

### DuckDB → Snowflake
```python
# Export to Parquet, then load to Snowflake
duckdb.execute("COPY int_all_awards TO 'awards.parquet'")
# Use Snowflake COPY INTO command
```

---

## Quick Start: Deploy to Streamlit Cloud NOW

**5-Minute Setup:**

1. **Create GitHub repo** (if not already)
   ```bash
   git init
   git add .
   git commit -m "ScholarHub data pipeline"
   git remote add origin https://github.com/YOUR_USERNAME/scholarhub.git
   git push -u origin main
   ```

2. **Go to** https://share.streamlit.io
3. **Click** "New app"
4. **Select** your repo: `YOUR_USERNAME/scholarhub`
5. **Main file:** `dashboard/app.py`
6. **Click** "Deploy"
7. **Get URL:** `https://scholarhub-YOUR_USERNAME.streamlit.app`

**Done!** Share the URL on your resume/LinkedIn.

---

## Next Steps

1. ✅ Deploy dashboard to Streamlit Cloud (5 min)
2. ✅ Set up GitHub Actions for daily pipeline (30 min)
3. ✅ Add custom domain (optional, $12/year)
4. ✅ Add to portfolio/resume with live link

**Total Time:** 30-60 minutes
**Total Cost:** $0/month (Streamlit free tier)
**Resume Impact:** "Deployed production data pipeline with automated daily updates"

---

## Questions?

- **Q: Will Streamlit Cloud handle 1,000 records?**
  - A: Yes, easily. Streamlit Cloud handles millions of rows with DuckDB.

- **Q: What if my .duckdb file is too large?**
  - A: Commit only recent data (last 2 years), store full history elsewhere.

- **Q: Can I add authentication?**
  - A: Yes, use `streamlit-authenticator` library or Streamlit's built-in auth (paid plans).

- **Q: How do I update data after deployment?**
  - A: GitHub Actions runs daily, automatically pushes updated .duckdb file.

**Ready to deploy?** Start with Streamlit Cloud - it's the fastest path to a live demo!
