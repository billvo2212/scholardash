"""
Institutions Page

BQ-5: Which institutions have the most funded capacity?

Analyzes institutional funding to identify universities with strong research
funding and capacity to support graduate students.
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def show(conn):
    """Render the Institutions page."""

    st.title("🏛️ Institution Analysis")

    st.markdown("""
    **Identify institutions with strong research funding and hiring capacity.**

    Institutions with high federal funding have more resources for graduate
    students: stipends, equipment, conferences, and research opportunities.
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
        """).fetchall()
        source_list = ["All"] + [s[0] for s in sources]
        selected_source = st.selectbox("Funding Source", source_list)

    with col2:
        # Metric to rank by
        rank_by = st.selectbox(
            "Rank institutions by",
            ["Total Funding", "Number of Awards", "Unique PIs", "Avg Award Size"]
        )

    with col3:
        # Top N institutions
        top_n = st.slider("Show top N institutions", 10, 50, 20, step=10)

    # ────────────────────────────────────────────────────────────────────────
    # Query Institution Data
    # ────────────────────────────────────────────────────────────────────────

    source_filter = "" if selected_source == "All" else f"AND source = '{selected_source}'"

    institution_query = f"""
        SELECT
            institution,
            COUNT(*) as num_awards,
            COUNT(DISTINCT pi_name) as num_unique_pis,
            SUM(funding_amount) / 1e6 as total_funding_millions,
            AVG(funding_amount) / 1e3 as avg_award_thousands,
            MIN(start_date) as first_award_date,
            MAX(start_date) as most_recent_award_date
        FROM analytics_intermediate.int_all_awards
        WHERE institution IS NOT NULL
          AND funding_amount IS NOT NULL
          {source_filter}
        GROUP BY institution
        ORDER BY total_funding_millions DESC
        LIMIT {top_n * 2}  -- Get more for filtering
    """

    institution_data = conn.execute(institution_query).df()

    if not institution_data.empty:

        # ────────────────────────────────────────────────────────────────────
        # Summary Metrics
        # ────────────────────────────────────────────────────────────────────

        col1, col2, col3, col4 = st.columns(4)

        total_institutions = conn.execute(f"""
            SELECT COUNT(DISTINCT institution)
            FROM analytics_intermediate.int_all_awards
            WHERE institution IS NOT NULL {source_filter}
        """).fetchone()[0]
        col1.metric("Total Institutions", f"{total_institutions:,}")

        total_funding = institution_data["total_funding_millions"].sum()
        col2.metric("Combined Funding", f"${total_funding:.1f}M")

        total_pis = institution_data["num_unique_pis"].sum()
        col3.metric("Total Researchers", f"{total_pis:,}")

        avg_inst_funding = institution_data["total_funding_millions"].mean()
        col4.metric("Avg per Institution", f"${avg_inst_funding:.1f}M")

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Ranking Based on Selected Metric
        # ────────────────────────────────────────────────────────────────────

        # Determine sorting column
        sort_mapping = {
            "Total Funding": "total_funding_millions",
            "Number of Awards": "num_awards",
            "Unique PIs": "num_unique_pis",
            "Avg Award Size": "avg_award_thousands"
        }
        sort_col = sort_mapping[rank_by]

        ranked_institutions = institution_data.sort_values(sort_col, ascending=False).head(top_n)

        st.subheader(f"🏆 Top {top_n} Institutions by {rank_by}")

        # Horizontal bar chart
        if rank_by == "Total Funding":
            y_val = "total_funding_millions"
            y_label = "Total Funding ($M)"
            color_scale = "Greens"
        elif rank_by == "Number of Awards":
            y_val = "num_awards"
            y_label = "Number of Awards"
            color_scale = "Blues"
        elif rank_by == "Unique PIs":
            y_val = "num_unique_pis"
            y_label = "Unique PIs"
            color_scale = "Purples"
        else:  # Avg Award Size
            y_val = "avg_award_thousands"
            y_label = "Avg Award Size ($K)"
            color_scale = "Oranges"

        fig = px.bar(
            ranked_institutions,
            x=y_val,
            y="institution",
            orientation="h",
            title=f"Top {top_n} by {rank_by}",
            labels={y_val: y_label, "institution": "Institution"},
            color=y_val,
            color_continuous_scale=color_scale
        )
        fig.update_layout(
            yaxis={"categoryorder": "total ascending"},
            showlegend=False,
            height=max(400, top_n * 25)
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Detailed Institution Table
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📊 Detailed Institution Rankings")

        # Format for display
        display_df = ranked_institutions.copy()
        display_df["total_funding_millions"] = display_df["total_funding_millions"].apply(
            lambda x: f"${x:.1f}M"
        )
        display_df["avg_award_thousands"] = display_df["avg_award_thousands"].apply(
            lambda x: f"${x:.0f}K"
        )
        display_df["first_award_date"] = display_df["first_award_date"].apply(
            lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) else "N/A"
        )
        display_df["most_recent_award_date"] = display_df["most_recent_award_date"].apply(
            lambda x: x.strftime("%Y-%m-%d") if pd.notna(x) else "N/A"
        )

        display_df = display_df[[
            "institution", "num_awards", "num_unique_pis",
            "total_funding_millions", "avg_award_thousands",
            "first_award_date", "most_recent_award_date"
        ]]
        display_df.columns = [
            "Institution", "Awards", "Unique PIs",
            "Total Funding", "Avg Award", "First Award", "Latest Award"
        ]

        st.dataframe(display_df, use_container_width=True, hide_index=True, height=500)

        # ────────────────────────────────────────────────────────────────────
        # Scatter Plot: Funding vs Research Capacity
        # ────────────────────────────────────────────────────────────────────

        st.markdown("---")
        st.subheader("💡 Funding vs Research Capacity")

        st.markdown("""
        **How to read this chart:**
        - **X-axis:** Total funding (size of research budget)
        - **Y-axis:** Number of unique PIs (diversity of research groups)
        - **Bubble size:** Average award size
        - **Top-right quadrant:** Large budgets + many PIs = high capacity
        """)

        fig = px.scatter(
            ranked_institutions,
            x="total_funding_millions",
            y="num_unique_pis",
            size="avg_award_thousands",
            hover_name="institution",
            hover_data={
                "num_awards": True,
                "total_funding_millions": ":.1f",
                "num_unique_pis": True,
                "avg_award_thousands": ":.0f"
            },
            labels={
                "total_funding_millions": "Total Funding ($M)",
                "num_unique_pis": "Number of Unique PIs",
                "avg_award_thousands": "Avg Award Size ($K)"
            },
            title="Institution Funding vs Research Capacity",
            color="num_awards",
            color_continuous_scale="Viridis"
        )
        fig.update_traces(marker=dict(sizemode="area", sizeref=2.*max(ranked_institutions["avg_award_thousands"])/(40.**2), line_width=1))
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Funding Distribution
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📈 Funding Concentration")

        # Calculate cumulative funding percentage
        all_institutions_sorted = institution_data.sort_values("total_funding_millions", ascending=False).reset_index(drop=True)
        all_institutions_sorted["cumulative_funding"] = all_institutions_sorted["total_funding_millions"].cumsum()
        total_all_funding = all_institutions_sorted["total_funding_millions"].sum()
        all_institutions_sorted["cumulative_pct"] = 100 * all_institutions_sorted["cumulative_funding"] / total_all_funding
        all_institutions_sorted["institution_rank"] = range(1, len(all_institutions_sorted) + 1)

        # Find top 10, top 20, top 50 percentages
        top_10_pct = all_institutions_sorted.iloc[9]["cumulative_pct"] if len(all_institutions_sorted) >= 10 else all_institutions_sorted.iloc[-1]["cumulative_pct"]
        top_20_pct = all_institutions_sorted.iloc[19]["cumulative_pct"] if len(all_institutions_sorted) >= 20 else all_institutions_sorted.iloc[-1]["cumulative_pct"]

        col1, col2, col3 = st.columns(3)
        col1.metric("Top 10 institutions capture", f"{top_10_pct:.1f}%")
        col2.metric("Top 20 institutions capture", f"{top_20_pct:.1f}%")
        col3.metric("Total institutions", len(all_institutions_sorted))

        # Pareto chart
        fig = px.line(
            all_institutions_sorted.head(50),
            x="institution_rank",
            y="cumulative_pct",
            title="Cumulative Funding Distribution (Pareto Chart)",
            labels={
                "institution_rank": "Institution Rank",
                "cumulative_pct": "Cumulative % of Total Funding"
            },
            markers=True
        )
        fig.add_hline(y=80, line_dash="dash", line_color="red", annotation_text="80% threshold")
        fig.update_layout(hovermode="x")
        st.plotly_chart(fig, use_container_width=True)

        st.caption("Funding is highly concentrated: a small number of elite institutions capture the majority of federal research dollars.")

        # ────────────────────────────────────────────────────────────────────
        # Download Data
        # ────────────────────────────────────────────────────────────────────

        st.markdown("---")
        csv_data = ranked_institutions.to_csv(index=False)
        st.download_button(
            label="📥 Download institution rankings as CSV",
            data=csv_data,
            file_name=f"institution_rankings_{selected_source.lower()}.csv",
            mime="text/csv"
        )

    else:
        st.info("No institution data available for selected filters.")

    # ────────────────────────────────────────────────────────────────────────
    # Strategy Guide
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    with st.expander("💡 How to Use This Analysis"):
        st.markdown("""
        **High Funding + High PI Count = Best Opportunities**
        - More research groups = more positions
        - Diverse faculty = better mentorship fit
        - Large budgets = better stipends and resources

        **High Funding + Low PI Count = Elite Small Programs**
        - Concentrated resources per student
        - Often more selective
        - Strong mentorship (fewer students per PI)

        **Moderate Funding + High PI Count = Breadth**
        - Many options across fields
        - May have resource constraints
        - Good for exploratory research

        **Strategy by Career Stage:**
        - **Early PhD applicants:** Target top 20 for resources and reputation
        - **Postdocs:** Focus on PI fit, not just institution rank
        - **International students:** Large institutions often have better visa support

        **Red Flags:**
        - Institutions with declining awards (check trends page)
        - Very low average award size (<$100K) may indicate limited capacity
        - No recent awards in your field
        """)
