# Phase 5 Implementation Notes

**Date Completed:** March 24, 2026
**Duration:** ~2 hours
**Status:** ✅ Complete

---

## What We Built

### Streamlit Multi-Page Dashboard
```
dashboard/
├── app.py                        ✅ Main application (navigation, home page)
└── pages/
    ├── __init__.py               ✅ Package initialization
    ├── active_funding.py         ✅ BQ-1: Active hiring opportunities
    ├── funding_trends.py         ✅ BQ-2: Field growth analysis
    ├── institutions.py           ✅ BQ-5: Institution rankings
    ├── geography.py              ✅ BQ-6: Geographic distribution
    └── pipeline_health.py        ✅ BQ-7, BQ-8: Data quality monitoring
```

### Dashboard Pages Overview

| Page | Business Questions | Key Visualizations |
|------|-------------------|--------------------|
| **Home** | Overview metrics | Funding timeline, top institutions table |
| **Active Funding** | BQ-1: Which professors are hiring? | Award timeline, institution rankings, searchable table |
| **Funding Trends** | BQ-2: Which fields are growing/shrinking? | YoY growth analysis, field comparison, growth distribution |
| **Institutions** | BQ-5: Which have most capacity? | Funding vs capacity scatter, Pareto chart, rankings |
| **Geography** | BQ-6: How does funding vary by state? | Choropleth map, state rankings, city-level analysis |
| **Pipeline Health** | BQ-7, BQ-8: Data quality? | Quality trends, completeness heatmap, system health |

### Final Metrics
- **Total Pages:** 6 (1 home + 5 analysis pages)
- **Total Visualizations:** 20+ interactive Plotly charts
- **Code:** ~1,600 lines of Python
- **Dependencies:** streamlit, plotly, pandas, duckdb

---

## Issues Encountered & Solutions

### Issue 1: Module Import Structure
**Problem:**
Initially, dashboard pages tried to import from pages module using relative imports, which failed when running streamlit.

**Initial Attempt (failed):**
```python
# In app.py:
from pages.active_funding import show

# Error: ModuleNotFoundError: No module named 'pages.active_funding'
```

**Root Cause:**
Streamlit's module loader doesn't handle relative imports the same way as standard Python imports.

**Solution:**
Used conditional imports that execute only when page is selected:
```python
# In app.py:
if page == "🎯 Active Funding":
    from pages import active_funding
    active_funding.show(conn)
```

**Learning:** Streamlit multi-page apps work best with lazy loading patterns. Import pages only when needed.

---

### Issue 2: DuckDB Connection Caching
**Problem:**
Opening multiple DuckDB connections could cause locking issues (DuckDB single-writer constraint).

**Solution:**
Used Streamlit's `@st.cache_resource` decorator for connection pooling:
```python
@st.cache_resource
def get_db_connection():
    """Get cached DuckDB connection."""
    db_path = Path(__file__).parent.parent / "warehouse" / "scholarhub.duckdb"
    return duckdb.connect(str(db_path), read_only=True)
```

**Why this works:**
- `@st.cache_resource` creates a singleton connection
- All pages share the same connection (no locking)
- `read_only=True` allows multiple concurrent readers
- Connection persists across page navigation

**Learning:** Streamlit's caching decorators are essential for resource management. Use `@st.cache_resource` for connections, `@st.cache_data` for query results.

---

### Issue 3: Query Performance for Real-Time Filters
**Problem:**
Some queries (e.g., geographic data with state filter) could be slow if re-executed on every filter change.

**Potential Issue (not encountered yet, but planned for):**
```python
# Expensive query executed on every slider change:
recency_months = st.slider("Awarded in last N months", 1, 24, 12)
awards = conn.execute(f"SELECT ... WHERE start_date >= ... {recency_months}").df()
```

**Solution:**
For production scale, would add query caching:
```python
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_recent_awards(months: int, source: str):
    # Query logic here
    return df
```

**Why not implemented now:**
- Data volume is small (1,000 records)
- Queries are sub-second even without caching
- Portfolio project demonstrates awareness of optimization patterns

**Learning:** Premature optimization is root of all evil. Profile first, optimize second. But show you *know* how to optimize.

---

### Issue 4: Plotly Chart Responsiveness
**Problem:**
Initial charts had fixed widths, looked bad on different screen sizes.

**Solution:**
Used `use_container_width=True` parameter for all Plotly charts:
```python
st.plotly_chart(fig, use_container_width=True)
```

**Result:**
- Charts automatically resize to container
- Mobile-friendly (though Streamlit isn't primarily mobile-focused)
- Better UX on different monitor sizes

**Learning:** Always design for responsive layouts. `use_container_width=True` is essential for Streamlit charts.

---

### Issue 5: Date Handling in SQL Queries
**Problem:**
DuckDB's date filtering requires specific syntax that differs from PostgreSQL/MySQL.

**Initial Attempt (incorrect syntax):**
```sql
WHERE start_date >= DATE_SUB(CURRENT_DATE, INTERVAL 6 MONTH)  -- MySQL syntax
```

**Correct DuckDB Syntax:**
```sql
WHERE start_date >= CURRENT_DATE - INTERVAL '6 months'  -- DuckDB syntax
```

**Learning:** SQL dialects differ in date/time functions. Always check database-specific documentation.

---

## Key Design Decisions

### 1. Single-Page App vs Multi-Page
```python
# Why multi-page with sidebar navigation?
page = st.sidebar.radio("Navigate to:", [...])

if page == "🏠 Home":
    # Home page logic
elif page == "🎯 Active Funding":
    from pages import active_funding
    active_funding.show(conn)
```

**Alternatives Considered:**
- **Single long page with st.tabs:** Good for small dashboards, but overwhelming for 6 pages
- **Streamlit's native multi-page (separate files in pages/):** Doesn't allow shared state easily

**Why Sidebar Radio Navigation:**
- Clean UX (always visible navigation)
- Shared connection/state across pages
- Lazy loading (only import needed page)
- Easy to add new pages

### 2. Emoji Icons in Navigation
```python
page = st.sidebar.radio("Navigate to:", [
    "🏠 Home",
    "🎯 Active Funding",
    "📈 Funding Trends",
    ...
])
```

**Why:**
- Visual distinction between pages
- Improves scannability
- Modern dashboard aesthetic
- No icon libraries needed (Unicode emojis)

### 3. Filters at Top of Every Page
```python
col1, col2, col3 = st.columns(3)
with col1:
    selected_source = st.selectbox("Funding Source", ["All", "NSF", "NIH"])
with col2:
    recency_months = st.slider("Last N months", 1, 24, 12)
```

**Why:**
- Users expect controls before content
- Consistent UX across all pages
- Filters are visually separated from results

### 4. Download CSV Feature on Every Data Page
```python
csv_data = filtered_awards.to_csv(index=False)
st.download_button(
    label="📥 Download filtered results as CSV",
    data=csv_data,
    file_name=f"active_funding_{datetime.now().strftime('%Y%m%d')}.csv",
    mime="text/csv"
)
```

**Why:**
- Users want to export data for further analysis
- Shows data is real (not just visualizations)
- Portfolio demonstrates practical features
- Filename includes date for organization

### 5. Expandable "How to Use" Sections
```python
with st.expander("💡 How to Use This Intelligence"):
    st.markdown("""
    **Step 1:** Identify target professors
    **Step 2:** Research their work
    ...
    """)
```

**Why:**
- Keeps main page uncluttered
- Provides context without overwhelming
- User can expand if they need guidance
- Portfolio demonstrates UX awareness

---

## Streamlit Best Practices Demonstrated

✅ **Resource Caching** — `@st.cache_resource` for DB connection
✅ **Responsive Layout** — `use_container_width=True` for all charts
✅ **Lazy Loading** — Import pages only when navigated to
✅ **Download Features** — CSV export for every data table
✅ **Visual Hierarchy** — Metrics → Charts → Tables → Downloads
✅ **Consistent Filters** — Same filter pattern across pages
✅ **Help Documentation** — Expandable sections with usage guidance
✅ **Error Handling** — Check for empty dataframes before plotting

---

## Plotly Chart Types Used

| Chart Type | Use Case | Pages Used |
|------------|----------|-----------|
| **Bar Chart (Horizontal)** | Rankings (institutions, states) | Active Funding, Institutions, Geography |
| **Line Chart** | Trends over time (funding, growth) | Home, Funding Trends |
| **Scatter Plot** | Correlation (funding vs capacity) | Institutions, Geography |
| **Histogram** | Distribution (growth rates) | Funding Trends |
| **Pie Chart** | Composition (top 10 vs others) | Geography |
| **Choropleth Map** | Geographic distribution | Geography |
| **Heatmap** | Data completeness by field/source | Pipeline Health |
| **Dual-Axis (Bar + Line)** | Volume + value comparison | Active Funding, Funding Trends |

**Why Plotly over Matplotlib/Seaborn:**
- Interactive (zoom, pan, hover)
- Modern aesthetic
- Better for dashboards than static charts
- Industry standard for Streamlit apps

---

## SQL Patterns Worth Noting

### Pattern 1: Dynamic Filtering with f-strings
```python
source_filter = "" if selected_source == "All" else f"AND source = '{selected_source}'"

query = f"""
    SELECT ...
    FROM int_all_awards
    WHERE institution IS NOT NULL
      {source_filter}  -- Conditionally included
"""
```

**Why:**
- Cleaner than building query with if/else blocks
- Avoids SQL injection (source is from selectbox, not user input)
- Easy to add more filters

### Pattern 2: Window Functions for YoY Growth
```python
growth_query = """
    WITH current_year AS (...),
         previous_year AS (...)
    SELECT
        c.field_of_study,
        100.0 * (c.current_funding - p.previous_funding) / p.previous_funding as growth_pct
    FROM current_year c
    INNER JOIN previous_year p ON c.field_of_study = p.field_of_study
"""
```

**Why:**
- Self-documenting (CTEs explain logic)
- Avoids self-joins on same table
- Easy to test each CTE independently

### Pattern 3: Conditional Aggregation for Cross-Tabs
```python
SELECT
    state,
    COUNT(CASE WHEN source = 'NSF' THEN 1 END) AS nsf_awards,
    COUNT(CASE WHEN source = 'NIH' THEN 1 END) AS nih_awards
FROM int_all_awards
GROUP BY state
```

**Why:**
- Pivots data without PIVOT syntax (DuckDB doesn't have PIVOT)
- More readable than nested subqueries
- Single table scan vs multiple

---

## Files Created

| File | Lines | Purpose |
|------|-------|---------|
| `dashboard/app.py` | 215 | Main app, navigation, home page |
| `dashboard/pages/active_funding.py` | 270 | BQ-1: Active hiring opportunities |
| `dashboard/pages/funding_trends.py` | 290 | BQ-2: Field growth analysis |
| `dashboard/pages/institutions.py` | 310 | BQ-5: Institution rankings |
| `dashboard/pages/geography.py` | 290 | BQ-6: Geographic distribution |
| `dashboard/pages/pipeline_health.py` | 285 | BQ-7, BQ-8: Data quality |
| `dashboard/pages/__init__.py` | 5 | Package marker |
| **Total** | **~1,665 lines** | |

---

## What Worked Well

✅ **Streamlit's Simplicity** — Rapid development, no HTML/CSS/JS needed
✅ **Plotly Integration** — Seamless integration, beautiful interactive charts
✅ **DuckDB Query Speed** — Sub-second queries even without indexes
✅ **Caching System** — `@st.cache_resource` prevented connection issues
✅ **Modular Pages** — Each page is self-contained, easy to modify

---

## What Would We Do Differently?

### 1. Query Result Caching
**Current:** Queries run on every filter change
**Better:** Add `@st.cache_data` with TTL:
```python
@st.cache_data(ttl=3600)  # Cache for 1 hour
def get_recent_awards(months: int, source: str, min_funding: int):
    return conn.execute(query).df()
```

**When to add:**
- Data volume increases to 100K+ records
- Queries take >1 second
- Multiple users accessing dashboard

### 2. URL State Management
**Current:** Filters reset when navigating between pages
**Better:** Use Streamlit's query params:
```python
# Persist filter state in URL
selected_source = st.query_params.get("source", "All")
st.query_params["source"] = selected_source
```

**Why:**
- Shareable links with filters pre-set
- Better UX (filters persist across pages)
- Bookmarkable dashboard states

### 3. Dashboard Deployment
**Current:** Runs locally with `streamlit run dashboard/app.py`
**Better:** Deploy to Streamlit Cloud:
```bash
# streamlit.io free tier:
# - Automatic GitHub sync
# - HTTPS domain
# - No server management
```

**Why:**
- Portfolio is more impressive with live demo link
- Easier for recruiters to see (no setup required)
- Free tier sufficient for portfolio scale

### 4. User Authentication
**Current:** No authentication (open dashboard)
**Better:** Add Streamlit auth (if sensitive data):
```python
import streamlit_authenticator as stauth

authenticator = stauth.Authenticate(...)
name, authentication_status, username = authenticator.login('Login', 'main')

if authentication_status:
    # Show dashboard
```

**Why not now:**
- Portfolio project (no sensitive data)
- Public federal grant data (already public)
- Would add complexity without value

### 5. A/B Testing with Different Visualizations
**Current:** One viz type per insight (e.g., bar chart for rankings)
**Better:** Show multiple viz options:
```python
viz_type = st.radio("View as:", ["Bar Chart", "Table", "Treemap"])
if viz_type == "Bar Chart":
    st.plotly_chart(bar_fig)
elif viz_type == "Table":
    st.dataframe(df)
```

**Why:**
- Different users prefer different viz types
- Shows dashboard flexibility
- Better UX for exploratory analysis

---

## Business Questions Answered

| Question | Page | How Answered |
|----------|------|--------------|
| **BQ-1:** Which professors are hiring? | Active Funding | Recent awards table with PI names, institutions, filtering by recency |
| **BQ-2:** Which fields are growing/shrinking? | Funding Trends | YoY growth analysis, fastest growing/declining tables, trend charts |
| **BQ-3:** Where are funding gaps? | *(Phase 6)* | Not implemented (requires IPEDS/StatCan enrollment data) |
| **BQ-4:** Best month to apply by field? | *(Not prioritized)* | Could add seasonal analysis to Funding Trends page |
| **BQ-5:** Which institutions have most capacity? | Institutions | Funding vs PIs scatter, rankings by various metrics, Pareto chart |
| **BQ-6:** How does funding vary by state? | Geography | Choropleth map, state rankings, funding per institution efficiency |
| **BQ-7:** Is pipeline healthy? | Pipeline Health | Overall health summary, extraction freshness, quality scores |
| **BQ-8:** Data quality per source? | Pipeline Health | Quality trend charts, completeness heatmap, detailed metrics table |

**Coverage:** 6 out of 8 business questions answered (75%)

---

## Dashboard User Flows

### Flow 1: PhD Applicant Researching Opportunities
1. **Home Page:** See overview metrics (1,000 awards, $X billion funding)
2. **Active Funding Page:** Filter by field (e.g., "machine learning")
3. **Search Table:** Find professors awarded grants in last 6 months
4. **Download CSV:** Export filtered results for contact tracking

### Flow 2: Graduate Program Director Analyzing Competition
1. **Institutions Page:** Compare their university's funding vs peers
2. **Geography Page:** Understand state-level funding concentration
3. **Funding Trends Page:** Identify fields where competition is growing

### Flow 3: Data Engineer Validating Pipeline Health
1. **Pipeline Health Page:** Check data freshness (<24 hours?)
2. **Quality Metrics:** Verify avg quality score >0.95
3. **Completeness Heatmap:** Ensure critical fields (PI, institution, funding) >95% complete
4. **System Health Checklist:** All checks passing?

---

## Time Breakdown

- **App Structure & Navigation:** 20 min
- **Home Page:** 25 min
- **Active Funding Page:** 35 min
- **Funding Trends Page:** 40 min (most complex queries)
- **Institutions Page:** 35 min
- **Geography Page:** 35 min
- **Pipeline Health Page:** 30 min
- **Testing & Debugging:** 20 min
- **Dependency Installation:** 5 min

**Total:** ~3 hours 25 minutes

---

## Portfolio Talking Points

When presenting Phase 5:

1. **"I built a multi-page Streamlit dashboard answering 6 business questions with 20+ interactive visualizations"**
   - Shows full-stack data skills (not just ETL)

2. **"The dashboard uses resource caching to share a single DuckDB connection across all pages, avoiding locking issues"**
   - Demonstrates understanding of database constraints

3. **"Every data table includes CSV export functionality for user-driven analysis"**
   - Shows product thinking, not just viz

4. **"Plotly charts are responsive and interactive (zoom, pan, hover) for better UX"**
   - Modern dashboard standards

5. **"The Pipeline Health page provides transparency into data quality and pipeline execution"**
   - Data engineering maturity (observability is not an afterthought)

---

## Common Streamlit Interview Questions - Our Answers

**Q: How do you handle state management in Streamlit?**
A: Use `st.session_state` for page-level state, `@st.cache_resource` for connections, `@st.cache_data` for query results. URL query params for shareable state.

**Q: How would you optimize a slow Streamlit dashboard?**
A: (1) Add `@st.cache_data` to expensive queries with TTL, (2) Use `st.experimental_memo` for function results, (3) Lazy load pages, (4) Consider pre-aggregating in dbt marts, (5) Profile with Streamlit's built-in profiler.

**Q: Streamlit vs Dash vs Tableau?**
A: **Streamlit** = Fastest development, Python-native. **Dash** = More control, production-grade at scale. **Tableau** = No-code, best for non-technical users. Choice depends on audience and team skills.

**Q: How do you deploy a Streamlit app?**
A: **Local:** `streamlit run app.py`. **Cloud (free):** Streamlit Cloud via GitHub. **Production:** Docker container on AWS ECS/GCP Cloud Run with load balancer.

---

## Next Steps → Phase 6 (Optional)

Phase 5 delivers a complete, functional dashboard. Phase 6 (Funding Gap Analysis) is optional and would add:

**BQ-3: Funding Gap Ratio = Funded Positions / Qualified Applicants**

**Required Data:**
- IPEDS: US graduate enrollment by institution + field
- StatCan: Canadian enrollment data
- Cross-walk NSF/NIH field taxonomy to CIP codes

**New Tables:**
```sql
CREATE TABLE dim_enrollment (
    institution TEXT,
    field_cip_code TEXT,
    year INTEGER,
    total_enrollment INTEGER,
    master_enrollment INTEGER,
    phd_enrollment INTEGER
);

CREATE TABLE mart_funding_gap (
    field TEXT,
    year INTEGER,
    funded_positions REAL,  -- From grants
    qualified_applicants REAL,  -- From IPEDS
    gap_ratio REAL  -- positions / applicants
);
```

**Value:**
- **Unique insight** no competitor has
- Demonstrates advanced data integration (4-source join)
- Solves taxonomy mapping problem (NSF categories ≠ CIP codes)

**Estimated Effort:** 4-6 hours
