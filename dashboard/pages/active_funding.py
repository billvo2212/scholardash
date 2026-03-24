"""
Active Funding Page

BQ-1: Which professors are actively hiring PhD students RIGHT NOW?

Identifies professors who recently received grants (NSF, NIH) and are likely
recruiting students before posting public job ads.
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta


def show(conn):
    """Render the Active Funding page."""

    st.title("🎯 Active Funding Opportunities")

    st.markdown("""
    **Find professors likely hiring BEFORE they post public ads.**

    Professors who received grants in the past 6-12 months are actively building
    research teams. Contact them directly to get ahead of the competition.
    """)

    # ────────────────────────────────────────────────────────────────────────
    # Filters
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    col1, col2, col3 = st.columns(3)

    with col1:
        # Source filter
        sources = conn.execute("""
            SELECT DISTINCT source FROM analytics_intermediate.int_all_awards ORDER BY source
        """).df()["source"].tolist()
        selected_source = st.selectbox("Funding Source", ["All"] + sources)

    with col2:
        # Recency filter (months)
        recency_months = st.slider("Awarded in last N months", 1, 24, 12)

    with col3:
        # Minimum funding amount
        min_funding = st.number_input(
            "Min Funding ($K)",
            min_value=0,
            max_value=10000,
            value=100,
            step=50
        )

    # ────────────────────────────────────────────────────────────────────────
    # Query Recent Awards
    # ────────────────────────────────────────────────────────────────────────

    source_filter = "" if selected_source == "All" else f"AND source = '{selected_source}'"

    recent_awards_query = f"""
        SELECT
            pi_name,
            institution,
            source,
            title,
            funding_amount,
            start_date,
            end_date,
            program_name,
            award_id,
            DATEDIFF('month', start_date, CURRENT_DATE) as months_since_award
        FROM analytics_intermediate.int_all_awards
        WHERE start_date >= CURRENT_DATE - INTERVAL '{recency_months} months'
          AND funding_amount >= {min_funding * 1000}
          {source_filter}
          AND pi_name IS NOT NULL
          AND institution IS NOT NULL
        ORDER BY start_date DESC
        LIMIT 500
    """

    recent_awards = conn.execute(recent_awards_query).df()

    # ────────────────────────────────────────────────────────────────────────
    # Summary Metrics
    # ────────────────────────────────────────────────────────────────────────

    if not recent_awards.empty:
        col1, col2, col3, col4 = st.columns(4)

        unique_pis = recent_awards["pi_name"].nunique()
        col1.metric("Active Researchers", f"{unique_pis:,}")

        total_funding = recent_awards["funding_amount"].sum() / 1e6
        col2.metric("Total Funding", f"${total_funding:.1f}M")

        unique_institutions = recent_awards["institution"].nunique()
        col3.metric("Institutions", unique_institutions)

        avg_award = recent_awards["funding_amount"].mean() / 1e3
        col4.metric("Avg Award", f"${avg_award:.0f}K")

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Funding Timeline
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📅 Award Timeline")

        # Group by month
        timeline_df = conn.execute(f"""
            SELECT
                DATE_TRUNC('month', start_date) as award_month,
                COUNT(*) as num_awards,
                SUM(funding_amount) / 1e6 as funding_millions
            FROM analytics_intermediate.int_all_awards
            WHERE start_date >= CURRENT_DATE - INTERVAL '{recency_months} months'
              AND funding_amount >= {min_funding * 1000}
              {source_filter}
            GROUP BY award_month
            ORDER BY award_month
        """).df()

        if not timeline_df.empty:
            fig = go.Figure()
            fig.add_trace(go.Bar(
                x=timeline_df["award_month"],
                y=timeline_df["num_awards"],
                name="Number of Awards",
                yaxis="y1",
                marker_color="lightblue"
            ))
            fig.add_trace(go.Scatter(
                x=timeline_df["award_month"],
                y=timeline_df["funding_millions"],
                name="Funding ($M)",
                yaxis="y2",
                mode="lines+markers",
                marker_color="darkgreen",
                line=dict(width=3)
            ))
            fig.update_layout(
                title="Monthly Award Activity",
                xaxis_title="Month",
                yaxis=dict(title="Number of Awards", side="left"),
                yaxis2=dict(title="Funding ($M)", overlaying="y", side="right"),
                hovermode="x unified",
                height=400
            )
            st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Top Hiring Institutions
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🏛️ Most Active Institutions (by recent awards)")

        top_institutions = recent_awards.groupby("institution").agg({
            "award_id": "count",
            "funding_amount": "sum",
            "pi_name": "nunique"
        }).reset_index()
        top_institutions.columns = ["Institution", "Awards", "Total Funding", "Unique PIs"]
        top_institutions = top_institutions.sort_values("Awards", ascending=False).head(10)

        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                top_institutions,
                x="Awards",
                y="Institution",
                orientation="h",
                title="Top 10 Institutions by Award Count",
                labels={"Awards": "Number of Recent Awards"},
                color="Awards",
                color_continuous_scale="Blues"
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            # Format funding for display
            top_institutions["Total Funding"] = top_institutions["Total Funding"].apply(
                lambda x: f"${x/1e6:.2f}M"
            )
            st.dataframe(
                top_institutions[["Institution", "Awards", "Unique PIs", "Total Funding"]],
                use_container_width=True,
                hide_index=True,
                height=400
            )

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Recent Awards Table (Searchable)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🔍 Recent Awards (Searchable)")

        search_term = st.text_input(
            "Search by PI name, institution, or title",
            placeholder="e.g., MIT, cancer, machine learning"
        )

        # Filter by search term
        if search_term:
            mask = (
                recent_awards["pi_name"].str.contains(search_term, case=False, na=False) |
                recent_awards["institution"].str.contains(search_term, case=False, na=False) |
                recent_awards["title"].str.contains(search_term, case=False, na=False) |
                recent_awards["program_name"].str.contains(search_term, case=False, na=False)
            )
            filtered_awards = recent_awards[mask]
        else:
            filtered_awards = recent_awards

        st.caption(f"Showing {len(filtered_awards)} of {len(recent_awards)} awards")

        # Format for display
        display_df = filtered_awards[[
            "pi_name", "institution", "source", "title",
            "funding_amount", "start_date", "months_since_award"
        ]].copy()

        display_df["funding_amount"] = display_df["funding_amount"].apply(
            lambda x: f"${x/1000:.0f}K" if x else "N/A"
        )
        display_df["start_date"] = display_df["start_date"].apply(
            lambda x: x.strftime("%Y-%m-%d") if x else "N/A"
        )
        display_df.columns = [
            "PI Name", "Institution", "Source", "Title",
            "Funding", "Start Date", "Months Ago"
        ]

        st.dataframe(
            display_df,
            use_container_width=True,
            hide_index=True,
            height=500
        )

        # ────────────────────────────────────────────────────────────────────
        # Download Data
        # ────────────────────────────────────────────────────────────────────

        st.markdown("---")
        csv_data = filtered_awards.to_csv(index=False)
        st.download_button(
            label="📥 Download filtered results as CSV",
            data=csv_data,
            file_name=f"active_funding_{datetime.now().strftime('%Y%m%d')}.csv",
            mime="text/csv"
        )

    else:
        st.info("No recent awards found matching your filters. Try expanding the date range or reducing minimum funding.")

    # ────────────────────────────────────────────────────────────────────────
    # How to Use This Data
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    with st.expander("💡 How to Use This Intelligence"):
        st.markdown("""
        **Step 1: Identify Target Professors**
        - Focus on awards from the past 3-6 months (sweet spot for hiring)
        - Look for large grants (>$500K) — more capacity to hire
        - NIH R01s and NSF CAREER awards are prime indicators

        **Step 2: Research Their Work**
        - Read the award abstract (available in raw data)
        - Check recent publications (use Semantic Scholar API)
        - Understand their research trajectory

        **Step 3: Reach Out Directly**
        - Email before they post public ads
        - Reference their recent grant in your message
        - Show how your interests align with the funded project

        **Why This Works:**
        - Professors get funding 6-12 months before posting ads
        - Early contact = less competition
        - Shows initiative and research skills
        """)
