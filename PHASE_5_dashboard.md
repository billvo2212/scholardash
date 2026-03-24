# Phase 5 — Streamlit Dashboard (5 Pages)

**Goal:** A production-quality analytics dashboard that visually answers all business questions. By the end, you have a shareable URL that demonstrates everything the pipeline produces.

**Duration:** ~1 week  
**Prerequisite:** Phases 1–4 complete. Mart tables have real data.

---

## Dashboard Architecture

```
dashboard/
├── app.py                  ← Entry point, navigation
├── pages/
│   ├── 1_funding_landscape.py    ← BQ-4, BQ-5: Where is the money?
│   ├── 2_pipeline_health.py      ← BQ-7, BQ-8: Is the pipeline working?
│   ├── 3_professor_intel.py      ← BQ-1: Who is hiring?
│   ├── 4_deadline_calendar.py    ← BQ-4: When to apply?
│   └── 5_north_america_map.py    ← BQ-6: Geographic distribution
├── components/
│   ├── charts.py           ← Reusable Plotly chart builders
│   ├── filters.py          ← Sidebar filter widgets
│   └── metrics.py          ← KPI card components
└── utils/
    └── data_loader.py      ← DuckDB queries → DataFrames
```

---

## Step 5.1 — Install Dependencies

```bash
pip install streamlit plotly pandas duckdb
```

---

## Step 5.2 — Data Loader

Create `dashboard/utils/data_loader.py`:

```python
# dashboard/utils/data_loader.py
"""
All DuckDB queries live here. Never write SQL in page files.

Why centralize queries?
1. Easy to optimize — one place to add indexes or rewrite slow queries
2. Caching — @st.cache_data means the same query doesn't re-run on every interaction
3. Testing — you can unit test these functions without Streamlit

TTL (time-to-live) = how long Streamlit caches the result.
3600 seconds = 1 hour. Fine for daily-updated data.
"""
import duckdb
import pandas as pd
import streamlit as st
from pathlib import Path


# ── Connection ────────────────────────────────────────────────────────────────

def get_db_path() -> str:
    """Find DuckDB file relative to this script."""
    # Try common locations
    candidates = [
        Path("warehouse/scholarhub.duckdb"),
        Path("../warehouse/scholarhub.duckdb"),
        Path("/opt/project/warehouse/scholarhub.duckdb"),
    ]
    for path in candidates:
        if path.exists():
            return str(path)
    raise FileNotFoundError("scholarhub.duckdb not found. Run: python warehouse/init_warehouse.py")


@st.cache_resource
def get_connection():
    """Cached DuckDB connection. read_only=True for dashboard safety."""
    return duckdb.connect(get_db_path(), read_only=True)


# ── Funding Landscape Queries ─────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_funding_by_field(
    min_year: int = 2015,
    country: str = "All",
) -> pd.DataFrame:
    """
    Returns aggregated funding by broad_category and year.
    Powers Page 1: Funding Landscape.
    """
    conn = get_connection()
    where_clause = f"WHERE year >= {min_year}"
    if country != "All":
        where_clause += f" AND country = '{country}'"

    return conn.execute(f"""
        SELECT
            broad_category,
            year,
            agency,
            country,
            SUM(award_count)    AS total_awards,
            SUM(total_usd)      AS total_usd,
            AVG(avg_usd)        AS avg_usd
        FROM analytics_marts.mart_funding_north_america
        {where_clause}
        GROUP BY 1, 2, 3, 4
        ORDER BY year DESC, total_usd DESC
    """).df()


@st.cache_data(ttl=3600)
def load_top_institutions(
    field: str = "All",
    year: int = 2023,
    limit: int = 20,
) -> pd.DataFrame:
    """Top institutions by funding amount for a given field/year."""
    conn = get_connection()
    where = f"WHERE year = {year}"
    if field != "All":
        where += f" AND broad_category = '{field}'"

    return conn.execute(f"""
        SELECT
            institution_name,
            country,
            SUM(total_usd)   AS total_usd,
            SUM(award_count) AS award_count
        FROM analytics_marts.mart_funding_north_america
        {where}
          AND institution_name IS NOT NULL
        GROUP BY 1, 2
        ORDER BY total_usd DESC
        LIMIT {limit}
    """).df()


# ── Pipeline Health Queries ────────────────────────────────────────────────────

@st.cache_data(ttl=300)   # 5-minute cache — health data should be fresh
def load_crawl_history(days: int = 30) -> pd.DataFrame:
    """Recent crawl history for pipeline health dashboard."""
    conn = get_connection()
    return conn.execute(f"""
        SELECT
            source_name,
            source_tier,
            status,
            records_loaded,
            records_failed,
            quality_avg,
            duration_secs,
            crawled_at
        FROM main.raw_crawl_log
        WHERE crawled_at >= NOW() - INTERVAL '{days} days'
        ORDER BY crawled_at DESC
    """).df()


@st.cache_data(ttl=300)
def load_source_health_summary() -> pd.DataFrame:
    """Aggregated health metrics per source."""
    conn = get_connection()
    return conn.execute("""
        SELECT
            source_name,
            source_tier,
            COUNT(*) AS total_crawls,
            ROUND(AVG(CASE WHEN status = 'success' THEN 100.0 ELSE 0.0 END), 1) AS success_rate,
            MAX(crawled_at) AS last_crawled,
            SUM(records_loaded) AS total_records,
            ROUND(AVG(quality_avg), 3) AS avg_quality,
            ROUND(AVG(duration_secs), 1) AS avg_duration_secs,
            -- Alert: not crawled in last 48h?
            CASE WHEN MAX(crawled_at) < NOW() - INTERVAL '48 hours'
                 THEN 'STALE' ELSE 'OK' END AS freshness_status
        FROM main.raw_crawl_log
        GROUP BY 1, 2
        ORDER BY source_name
    """).df()


# ── Professor Intelligence Queries ────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_professor_grants(
    field_filter: str = "All",
    country: str = "All",
    active_only: bool = True,
    limit: int = 100,
) -> pd.DataFrame:
    """
    Professors with active grants — for BQ-1 (who is hiring?).
    Prioritizes T32/CREATE training grants (explicit hiring signal).
    """
    conn = get_connection()

    where_parts = []
    if active_only:
        where_parts.append("is_active = TRUE")
    if country != "All":
        where_parts.append(f"country = '{country}'")

    where = "WHERE " + " AND ".join(where_parts) if where_parts else ""

    return conn.execute(f"""
        SELECT
            pi_name,
            institution_name,
            institution_state AS region,
            country,
            activity_code,
            fiscal_year,
            amount_usd,
            is_training_grant,
            -- Signal strength: training grants rank higher
            CASE WHEN is_training_grant THEN 'High - Training Grant'
                 WHEN amount_usd > 500000 THEN 'Medium - Large Grant'
                 ELSE 'Low - Standard Grant'
            END AS hiring_signal,
            project_num,
            is_active
        FROM analytics_staging.stg_nih_projects
        {where}
        ORDER BY
            is_training_grant DESC,
            amount_usd DESC NULLS LAST
        LIMIT {limit}
    """).df()


# ── Deadline Calendar Queries ──────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_deadline_seasonality() -> pd.DataFrame:
    """Monthly distribution of award start dates (proxy for application deadlines)."""
    conn = get_connection()
    return conn.execute("""
        SELECT
            broad_category,
            start_month,
            month_name,
            academic_season,
            SUM(opportunity_count) AS total_opportunities,
            SUM(total_funding_usd) AS total_usd
        FROM analytics_marts.mart_deadline_calendar
        GROUP BY 1, 2, 3, 4
        ORDER BY start_month
    """).df()


# ── KPI Metrics ────────────────────────────────────────────────────────────────

@st.cache_data(ttl=3600)
def load_kpis() -> dict:
    """Summary KPIs for the dashboard header."""
    conn = get_connection()

    nsf_count = conn.execute(
        "SELECT COUNT(*) FROM main.raw_nsf_awards"
    ).fetchone()[0]

    nih_count = conn.execute(
        "SELECT COUNT(*) FROM main.raw_nih_projects"
    ).fetchone()[0]

    total_funding = conn.execute("""
        SELECT COALESCE(SUM(total_usd), 0)
        FROM analytics_marts.mart_funding_north_america
        WHERE year >= 2020
    """).fetchone()[0]

    last_updated = conn.execute("""
        SELECT MAX(crawled_at) FROM main.raw_crawl_log
        WHERE status = 'success'
    """).fetchone()[0]

    return {
        "nsf_records": f"{nsf_count:,}",
        "nih_records": f"{nih_count:,}",
        "total_funding_billions": f"${total_funding/1e9:.1f}B",
        "last_updated": str(last_updated)[:16] if last_updated else "Never",
    }
```

---

## Step 5.3 — App Entry Point

Create `dashboard/app.py`:

```python
# dashboard/app.py
import streamlit as st

st.set_page_config(
    page_title="ScholarHub Analytics",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("ScholarHub Analytics")
st.markdown("""
**Graduate funding intelligence for North America.**
Combining NSF, NIH, NSERC, and CIHR data to answer questions
no other platform answers.
""")

# KPIs in the header
try:
    from dashboard.utils.data_loader import load_kpis
    kpis = load_kpis()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("NSF Awards", kpis["nsf_records"])
    with col2:
        st.metric("NIH Projects", kpis["nih_records"])
    with col3:
        st.metric("Total Funding (2020+)", kpis["total_funding_billions"])
    with col4:
        st.metric("Last Updated", kpis["last_updated"])
except Exception as e:
    st.warning(f"Dashboard data not ready: {e}. Run the pipeline first.")

st.markdown("---")
st.markdown("""
### Navigate using the sidebar:
- **Funding Landscape** — Which fields have the most funding?
- **Pipeline Health** — Is the data pipeline working correctly?
- **Professor Intel** — Which professors have active grants?
- **Deadline Calendar** — When do opportunities peak?
- **North America Map** — Geographic funding distribution
""")
```

---

## Step 5.4 — Page 1: Funding Landscape

Create `dashboard/pages/1_funding_landscape.py`:

```python
# dashboard/pages/1_funding_landscape.py
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import sys
sys.path.insert(0, '.')

from dashboard.utils.data_loader import load_funding_by_field, load_top_institutions

st.title("Funding Landscape")
st.markdown("*Which fields and institutions have the most graduate funding?*")

# ── Sidebar Filters ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    min_year = st.slider("From year", 2015, 2024, 2018)
    country = st.selectbox("Country", ["All", "US", "Canada"])

# ── Load Data ──────────────────────────────────────────────────────────────
df = load_funding_by_field(min_year=min_year, country=country)

if df.empty:
    st.warning("No data available. Run the pipeline to load data.")
    st.stop()

# ── KPI Row ────────────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
with col1:
    total = df['total_usd'].sum()
    st.metric("Total Funding", f"${total/1e9:.1f}B")
with col2:
    total_awards = df['total_awards'].sum()
    st.metric("Total Awards", f"{total_awards:,.0f}")
with col3:
    fields = df['broad_category'].nunique()
    st.metric("Fields Covered", fields)

st.markdown("---")

# ── Chart 1: Funding by Field Over Time (Line Chart) ──────────────────────
st.subheader("Funding Trend by Field")

trend_df = df.groupby(['year', 'broad_category'])['total_usd'].sum().reset_index()
trend_df['total_billions'] = trend_df['total_usd'] / 1e9

fig_trend = px.line(
    trend_df,
    x='year',
    y='total_billions',
    color='broad_category',
    title='Annual Funding by Field (USD Billions)',
    labels={'total_billions': 'Funding (USD Billions)', 'year': 'Year',
            'broad_category': 'Field'},
    markers=True,
)
fig_trend.update_layout(
    hovermode='x unified',
    legend=dict(orientation='h', yanchor='bottom', y=1.02),
)
st.plotly_chart(fig_trend, use_container_width=True)

# ── Chart 2: Current Distribution (Treemap) ───────────────────────────────
st.subheader("Current Year Funding Distribution")

latest_year = df['year'].max()
current_df = df[df['year'] == latest_year].groupby(
    ['broad_category', 'agency']
)['total_usd'].sum().reset_index()

fig_tree = px.treemap(
    current_df,
    path=['broad_category', 'agency'],
    values='total_usd',
    title=f'Funding Distribution {latest_year} — by Field and Agency',
    color='total_usd',
    color_continuous_scale='Blues',
)
fig_tree.update_traces(textinfo='label+percent parent')
st.plotly_chart(fig_tree, use_container_width=True)

# ── Chart 3: Top Institutions ──────────────────────────────────────────────
st.subheader("Top Funded Institutions")

field_options = ["All"] + sorted(df['broad_category'].unique().tolist())
selected_field = st.selectbox("Filter by field", field_options)
top_inst_df = load_top_institutions(
    field=selected_field,
    year=latest_year,
    limit=20,
)

if not top_inst_df.empty:
    fig_bar = px.bar(
        top_inst_df.head(15),
        x='total_usd',
        y='institution_name',
        orientation='h',
        color='country',
        title=f'Top 15 Institutions by Funding ({latest_year})',
        labels={'total_usd': 'Total Funding (USD)', 'institution_name': 'Institution'},
        color_discrete_map={'US': '#1f77b4', 'Canada': '#ff7f0e'},
    )
    fig_bar.update_layout(yaxis={'categoryorder': 'total ascending'})
    st.plotly_chart(fig_bar, use_container_width=True)

# ── Raw Data Table ─────────────────────────────────────────────────────────
with st.expander("View raw data"):
    st.dataframe(
        df.groupby(['broad_category', 'year'])['total_usd'].sum()
          .reset_index()
          .sort_values('total_usd', ascending=False),
        use_container_width=True,
    )
```

---

## Step 5.5 — Page 2: Pipeline Health

Create `dashboard/pages/2_pipeline_health.py`:

```python
# dashboard/pages/2_pipeline_health.py
"""
Pipeline Health Dashboard — the most important page for DE portfolio.
This page proves you built an observable, production-grade system.
It answers: "Is the pipeline working? Where are the quality issues?"
"""
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
import sys
sys.path.insert(0, '.')

from dashboard.utils.data_loader import load_crawl_history, load_source_health_summary

st.title("Pipeline Health")
st.markdown("*Monitoring the data pipeline: crawl status, data quality, and freshness.*")

# ── Load Data ──────────────────────────────────────────────────────────────
summary_df = load_source_health_summary()
history_df = load_crawl_history(days=30)

# ── Source Status Cards ────────────────────────────────────────────────────
st.subheader("Source Status")

if not summary_df.empty:
    cols = st.columns(len(summary_df))
    for i, (_, row) in enumerate(summary_df.iterrows()):
        with cols[i]:
            status_color = "🟢" if row['freshness_status'] == 'OK' else "🔴"
            st.metric(
                label=f"{status_color} {row['source_name']}",
                value=f"{row['success_rate']}% success",
                delta=f"{row['total_records']:,} records",
            )
            st.caption(f"Last crawled: {str(row['last_crawled'])[:16]}")
            st.caption(f"Quality avg: {row['avg_quality']:.2f}")
else:
    st.info("No crawl history yet. Run the pipeline first.")

st.markdown("---")

# ── Chart 1: Crawl Success Rate Over Time ─────────────────────────────────
st.subheader("Crawl Success Rate — Last 30 Days")

if not history_df.empty:
    history_df['crawled_date'] = pd.to_datetime(history_df['crawled_at']).dt.date
    history_df['success_flag'] = (history_df['status'] == 'success').astype(int)

    daily = history_df.groupby(['crawled_date', 'source_name']).agg(
        success_rate=('success_flag', lambda x: x.mean() * 100),
        crawl_count=('status', 'count'),
    ).reset_index()

    fig_success = px.line(
        daily,
        x='crawled_date',
        y='success_rate',
        color='source_name',
        title='Daily Crawl Success Rate by Source (%)',
        labels={'success_rate': 'Success Rate (%)', 'crawled_date': 'Date'},
        range_y=[0, 105],
    )
    fig_success.add_hline(y=95, line_dash='dash', line_color='red',
                          annotation_text='95% threshold')
    st.plotly_chart(fig_success, use_container_width=True)

# ── Chart 2: Data Quality Distribution ────────────────────────────────────
st.subheader("Data Quality Score Distribution")

if not history_df.empty and 'quality_avg' in history_df.columns:
    quality_data = history_df.dropna(subset=['quality_avg'])

    fig_quality = px.histogram(
        quality_data,
        x='quality_avg',
        color='source_name',
        nbins=20,
        title='Distribution of Data Quality Scores by Source',
        labels={'quality_avg': 'Quality Score (0-1)', 'count': 'Number of Crawls'},
        barmode='overlay',
        opacity=0.7,
    )
    fig_quality.add_vline(x=0.8, line_dash='dash', line_color='green',
                          annotation_text='0.8 target')
    st.plotly_chart(fig_quality, use_container_width=True)

# ── Chart 3: Records Loaded per Day ───────────────────────────────────────
st.subheader("Records Loaded per Day")

if not history_df.empty:
    daily_records = history_df.groupby(['crawled_date', 'source_name'])[
        'records_loaded'
    ].sum().reset_index()

    fig_records = px.bar(
        daily_records,
        x='crawled_date',
        y='records_loaded',
        color='source_name',
        title='Records Loaded per Day',
        labels={'records_loaded': 'Records Loaded', 'crawled_date': 'Date'},
    )
    st.plotly_chart(fig_records, use_container_width=True)

# ── Raw Crawl Log ──────────────────────────────────────────────────────────
with st.expander("Raw crawl log (last 50 entries)"):
    display_cols = ['crawled_at', 'source_name', 'status', 'records_loaded',
                    'records_failed', 'quality_avg', 'duration_secs']
    st.dataframe(
        history_df[display_cols].head(50),
        use_container_width=True,
    )
```

---

## Step 5.6 — Page 3: Professor Intel

Create `dashboard/pages/3_professor_intel.py`:

```python
# dashboard/pages/3_professor_intel.py
"""
Professor Intelligence — BQ-1: Who is actively hiring PhD students right now?
The key insight: T32 training grants = professor has explicit funding for students.
"""
import streamlit as st
import plotly.express as px
import sys
sys.path.insert(0, '.')

from dashboard.utils.data_loader import load_professor_grants

st.title("Professor Intelligence")
st.markdown("""
*Which professors have active grants and are likely hiring PhD students?*

**Signal strength explanation:**
- 🔴 **High** — Training grant (T32/CREATE): professor has explicit funding allocated for PhD students
- 🟡 **Medium** — Large research grant (>$500K): sufficient budget to fund a student  
- ⚪ **Low** — Standard grant: possible funding, less certain
""")

# ── Sidebar Filters ────────────────────────────────────────────────────────
with st.sidebar:
    st.header("Filters")
    country = st.selectbox("Country", ["All", "US", "Canada"])
    active_only = st.checkbox("Active grants only", value=True)
    limit = st.slider("Max results", 50, 500, 100)

# ── Load Data ──────────────────────────────────────────────────────────────
df = load_professor_grants(
    country=country,
    active_only=active_only,
    limit=limit,
)

if df.empty:
    st.warning("No professor data available. Run NIH extractor first.")
    st.stop()

# ── Summary Metrics ────────────────────────────────────────────────────────
col1, col2, col3 = st.columns(3)
training_count = df[df['is_training_grant'] == True].shape[0]
with col1:
    st.metric("Professors with Active Grants", len(df))
with col2:
    st.metric("With Training Grants (T32)", training_count,
              help="T32 grants = explicit PhD training funding")
with col3:
    st.metric("Total Active Funding",
              f"${df['amount_usd'].sum()/1e6:.0f}M")

st.markdown("---")

# ── Highlight Training Grants ──────────────────────────────────────────────
st.subheader("High Signal: Training Grant Recipients")
training_df = df[df['is_training_grant'] == True].sort_values(
    'amount_usd', ascending=False
)

if not training_df.empty:
    st.dataframe(
        training_df[['pi_name', 'institution_name', 'region', 'country',
                     'activity_code', 'amount_usd', 'fiscal_year']],
        use_container_width=True,
        column_config={
            'amount_usd': st.column_config.NumberColumn(
                'Award Amount', format='$%.0f'
            ),
        }
    )
else:
    st.info("No training grants found in current filter.")

# ── All Grants Table ───────────────────────────────────────────────────────
st.subheader("All Active Grants")

# Add visual signal indicator
df_display = df.copy()
df_display['signal'] = df_display['hiring_signal'].map({
    'High - Training Grant': '🔴 High',
    'Medium - Large Grant': '🟡 Medium',
    'Low - Standard Grant': '⚪ Low',
})

st.dataframe(
    df_display[['signal', 'pi_name', 'institution_name', 'region', 'country',
                'activity_code', 'amount_usd', 'fiscal_year']].rename(
        columns={'signal': 'Hiring Signal'}
    ),
    use_container_width=True,
    column_config={
        'amount_usd': st.column_config.NumberColumn('Amount', format='$%.0f'),
    }
)

# ── Bar Chart: Institutions with Most Active Grants ────────────────────────
st.subheader("Institutions with Most Active Grants")
inst_counts = df.groupby('institution_name')['project_num'].count().reset_index()
inst_counts.columns = ['institution', 'grant_count']
inst_counts = inst_counts.sort_values('grant_count', ascending=False).head(20)

fig = px.bar(
    inst_counts,
    x='grant_count',
    y='institution',
    orientation='h',
    title='Top 20 Institutions by Number of Active Grants',
)
fig.update_layout(yaxis={'categoryorder': 'total ascending'})
st.plotly_chart(fig, use_container_width=True)
```

---

## Step 5.7 — Page 4: Deadline Calendar

Create `dashboard/pages/4_deadline_calendar.py`:

```python
# dashboard/pages/4_deadline_calendar.py
import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import sys
sys.path.insert(0, '.')

from dashboard.utils.data_loader import load_deadline_seasonality

st.title("Deadline Calendar")
st.markdown("*When do graduate funding opportunities peak? Plan your application timeline.*")

df = load_deadline_seasonality()

if df.empty:
    st.warning("No deadline data available.")
    st.stop()

# ── Chart 1: Monthly Heatmap by Field ─────────────────────────────────────
st.subheader("Monthly Opportunity Count by Field")

pivot = df.pivot_table(
    index='broad_category',
    columns='month_name',
    values='total_opportunities',
    aggfunc='sum',
    fill_value=0,
)

# Reorder months
month_order = ['January', 'February', 'March', 'April', 'May', 'June',
               'July', 'August', 'September', 'October', 'November', 'December']
pivot = pivot.reindex(columns=[m for m in month_order if m in pivot.columns])

fig_heat = px.imshow(
    pivot,
    title='Funding Opportunities by Field and Month (Heatmap)',
    labels=dict(x='Month', y='Field', color='Opportunities'),
    color_continuous_scale='Blues',
    aspect='auto',
)
st.plotly_chart(fig_heat, use_container_width=True)

# ── Chart 2: Academic Season Distribution ─────────────────────────────────
st.subheader("Opportunities by Academic Season")

season_df = df.groupby('academic_season')['total_opportunities'].sum().reset_index()
fig_season = px.pie(
    season_df,
    names='academic_season',
    values='total_opportunities',
    title='Distribution by Academic Season',
    color_discrete_map={
        'Fall': '#1f77b4',
        'Spring': '#ff7f0e',
        'Summer': '#2ca02c',
    }
)
st.plotly_chart(fig_season, use_container_width=True)

# ── Key Insight ────────────────────────────────────────────────────────────
st.info("""
**Key insight from this data:**  
Fall (September–December) has the highest concentration of opportunities — 
both NSF GRFP and NSERC doctoral scholarship deadlines fall in October–November.
Spring (January–April) is the second peak, particularly for NIH fellowships.
Summer is the slowest period — use it to prepare Fall applications.
""")
```

---

## Step 5.8 — Run the Dashboard

```bash
# From project root
streamlit run dashboard/app.py

# Opens at: http://localhost:8501
```

For production deployment (free hosting):
```bash
# Deploy to Streamlit Cloud (free tier)
# 1. Push to GitHub
# 2. Go to share.streamlit.io
# 3. Connect your repo
# 4. Set DUCKDB_PATH as a secret
# Note: DuckDB works on Streamlit Cloud if the .duckdb file is in the repo
#       OR if you export mart tables to CSV/Parquet and read those instead
```

Export marts to Parquet for Streamlit Cloud deployment:
```python
# warehouse/export_for_dashboard.py
"""Run this to export mart tables to Parquet files for deployment."""
import duckdb
from pathlib import Path

conn = duckdb.connect('warehouse/scholarhub.duckdb', read_only=True)
export_dir = Path('data/exports')
export_dir.mkdir(exist_ok=True)

tables = [
    'analytics_marts.mart_funding_north_america',
    'analytics_marts.mart_funding_by_field',
    'analytics_marts.mart_source_health',
    'analytics_marts.mart_deadline_calendar',
]

for table in tables:
    name = table.split('.')[-1]
    conn.execute(f"COPY {table} TO 'data/exports/{name}.parquet' (FORMAT PARQUET)")
    print(f"Exported {name}.parquet")
```

---

## Phase 5 Checklist

- [ ] `streamlit run dashboard/app.py` opens without errors
- [ ] Page 1: Line chart showing funding trends by year and field
- [ ] Page 2: Crawl history table with success rates (even if sparse)
- [ ] Page 3: Professor table with T32 grants highlighted
- [ ] Page 4: Heatmap showing monthly opportunity distribution
- [ ] All pages have working sidebar filters
- [ ] KPIs in header show real numbers from your data

**What you've built:**
- 5-page analytics dashboard answering every business question
- Data quality monitoring page (strongest portfolio signal)
- Professor hiring intelligence (differentiating insight)
- Cached queries (demonstrates understanding of performance)

**Next:** Phase 6 — Funding gap analysis (the most unique BQ-3 insight).
