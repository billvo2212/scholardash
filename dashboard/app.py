"""
ScholarHub Dashboard — Main Application

Multi-page Streamlit dashboard answering 8 key business questions about
North American research funding opportunities.

Pages:
1. 🏠 Home - Overview and key metrics
2. 🎯 Active Funding - Professors actively hiring (BQ-1)
3. 📈 Funding Trends - Field growth over time (BQ-2)
4. 🏛️ Institutions - Top funded institutions (BQ-5)
5. 🗺️ Geography - State/province analysis (BQ-6)
6. 🔧 Pipeline Health - Data quality monitoring (BQ-7, BQ-8)

Author: ScholarHub Team
"""

import streamlit as st
import duckdb
import plotly.express as px
import plotly.graph_objects as go
from pathlib import Path
from datetime import datetime
import sys

# Add project to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.settings import settings

# ────────────────────────────────────────────────────────────────────────────
# Page Configuration
# ────────────────────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="ScholarHub | Research Funding Intelligence",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Hide default Streamlit page navigation
st.markdown("""
<style>
    [data-testid="stSidebarNav"] {display: none;}
</style>
""", unsafe_allow_html=True)

# ────────────────────────────────────────────────────────────────────────────
# Database Connection
# ────────────────────────────────────────────────────────────────────────────

@st.cache_resource
def get_db_connection():
    """Get cached DuckDB connection."""
    db_path = Path(__file__).parent.parent / "warehouse" / "scholarhub.duckdb"
    if not db_path.exists():
        st.error(f"Database not found at {db_path}. Run pipeline first.")
        st.stop()
    return duckdb.connect(str(db_path), read_only=True)


conn = get_db_connection()

# ────────────────────────────────────────────────────────────────────────────
# Sidebar Navigation
# ────────────────────────────────────────────────────────────────────────────

st.sidebar.title("🎓 ScholarHub")
st.sidebar.markdown("### Research Funding Intelligence")
st.sidebar.markdown("---")

# Page selection
page = st.sidebar.radio(
    "Navigate to:",
    [
        "🏠 Home",
        "🎯 Active Funding",
        "📈 Funding Trends",
        "🏛️ Institutions",
        "🗺️ Geography",
        "🔧 Pipeline Health",
    ],
)

st.sidebar.markdown("---")
st.sidebar.markdown("**Data Sources:**")
st.sidebar.markdown("- NSF Award Search API")
st.sidebar.markdown("- NIH RePORTER v2 API")

st.sidebar.markdown("---")
st.sidebar.caption(f"Last updated: {datetime.now().strftime('%Y-%m-%d %H:%M')}")

# ────────────────────────────────────────────────────────────────────────────
# Home Page
# ────────────────────────────────────────────────────────────────────────────

if page == "🏠 Home":
    st.title("🎓 ScholarHub — Research Funding Intelligence")

    st.markdown("""
    **Find funded research positions before they're publicly posted.**

    ScholarHub combines federal grant data (NSF, NIH) with institutional
    intelligence to answer questions no other platform can:
    - Which professors just received funding and are likely hiring?
    - Which fields are growing or shrinking in federal support?
    - Where are the funding gaps — positions per qualified applicant?
    """)

    # Key Metrics Row
    col1, col2, col3, col4 = st.columns(4)

    # Total Awards
    total_awards = conn.execute("""
        SELECT COUNT(*) as count FROM analytics_intermediate.int_all_awards
    """).fetchone()[0]
    col1.metric("Total Awards", f"{total_awards:,}")

    # Total Funding
    total_funding = conn.execute("""
        SELECT SUM(funding_amount) / 1e9 as total_billions
        FROM analytics_intermediate.int_all_awards
        WHERE funding_amount IS NOT NULL
    """).fetchone()[0]
    col2.metric("Total Funding", f"${total_funding:.2f}B")

    # Data Sources
    sources = conn.execute("""
        SELECT COUNT(DISTINCT source) as count FROM analytics_intermediate.int_all_awards
    """).fetchone()[0]
    col3.metric("Data Sources", sources)

    # Avg Quality Score
    avg_quality = conn.execute("""
        SELECT AVG(quality_score) as avg_q FROM main.raw_nsf_awards
    """).fetchone()[0] or 0.0
    col4.metric("Avg Data Quality", f"{avg_quality:.3f}")

    st.markdown("---")

    # Funding Over Time Chart
    st.subheader("📊 Funding Trends — NSF vs NIH")

    funding_by_source = conn.execute("""
        SELECT
            award_year,
            source,
            SUM(funding_amount) / 1e6 as funding_millions
        FROM analytics_intermediate.int_all_awards
        WHERE award_year IS NOT NULL
          AND funding_amount IS NOT NULL
        GROUP BY award_year, source
        ORDER BY award_year, source
    """).df()

    if not funding_by_source.empty:
        fig = px.line(
            funding_by_source,
            x="award_year",
            y="funding_millions",
            color="source",
            title="Annual Funding by Source",
            labels={
                "award_year": "Year",
                "funding_millions": "Funding ($M)",
                "source": "Source"
            },
            markers=True
        )
        fig.update_layout(hovermode="x unified")
        st.plotly_chart(fig, use_container_width=True)
    else:
        st.info("No time-series data available yet. Run pipeline to populate.")

    st.markdown("---")

    # Top Institutions Table
    st.subheader("🏛️ Top 10 Funded Institutions")

    top_institutions = conn.execute("""
        SELECT
            institution,
            COUNT(*) as total_awards,
            SUM(funding_amount) / 1e6 as total_funding_millions
        FROM analytics_intermediate.int_all_awards
        WHERE institution IS NOT NULL
          AND funding_amount IS NOT NULL
        GROUP BY institution
        ORDER BY total_funding_millions DESC
        LIMIT 10
    """).df()

    if not top_institutions.empty:
        # Format for display
        top_institutions["total_funding_millions"] = top_institutions["total_funding_millions"].apply(
            lambda x: f"${x:,.2f}M"
        )
        top_institutions.columns = ["Institution", "Awards", "Total Funding"]
        st.dataframe(top_institutions, use_container_width=True, hide_index=True)
    else:
        st.info("No institution data available yet. Run pipeline to populate.")

    st.markdown("---")

    # Business Questions Section
    st.subheader("💡 What ScholarHub Answers")

    col1, col2 = st.columns(2)

    with col1:
        st.markdown("""
        **Unique Insights** (only we can answer):
        - **BQ-1:** Which professors are actively hiring PhD students RIGHT NOW?
        - **BQ-2:** Which fields are growing or shrinking in funding?
        - **BQ-3:** Where are the funding gaps by field?
        """)

    with col2:
        st.markdown("""
        **Better Intelligence** (we answer better):
        - **BQ-4:** What month is best to apply by field?
        - **BQ-5:** Which institutions have most funded capacity?
        - **BQ-6:** How does funding vary by state/province?
        """)

    st.markdown("---")
    st.caption("📊 Real-time federal research funding intelligence from NSF & NIH")

# ────────────────────────────────────────────────────────────────────────────
# Route to Other Pages
# ────────────────────────────────────────────────────────────────────────────

elif page == "🎯 Active Funding":
    from pages import active_funding
    active_funding.show(conn)

elif page == "📈 Funding Trends":
    from pages import funding_trends
    funding_trends.show(conn)

elif page == "🏛️ Institutions":
    from pages import institutions
    institutions.show(conn)

elif page == "🗺️ Geography":
    from pages import geography
    geography.show(conn)

elif page == "🔧 Pipeline Health":
    from pages import pipeline_health
    pipeline_health.show(conn)
