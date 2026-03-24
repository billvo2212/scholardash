"""
Funding Trends Page

BQ-2: Which fields are growing or shrinking in funding over time?

Analyzes year-over-year funding trends by academic field to identify
hot and cold research areas.
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def show(conn):
    """Render the Funding Trends page."""

    st.title("📈 Funding Trends by Field")

    st.markdown("""
    **Identify hot and cold research fields based on federal funding trends.**

    Growing fields indicate emerging priorities. Shrinking fields may have fewer
    opportunities. Use this intelligence to guide your research direction.
    """)

    # ────────────────────────────────────────────────────────────────────────
    # Filters
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    col1, col2 = st.columns(2)

    with col1:
        # Source filter
        sources = conn.execute("""
            SELECT DISTINCT source FROM analytics_intermediate.int_all_awards ORDER BY source
        """).fetchall()
        source_list = ["All"] + [s[0] for s in sources]
        selected_source = st.selectbox("Funding Source", source_list)

    with col2:
        # Year range
        year_range = conn.execute("""
            SELECT
                MIN(award_year) as min_year,
                MAX(award_year) as max_year
            FROM analytics_intermediate.int_all_awards
            WHERE award_year IS NOT NULL
        """).fetchone()

        if year_range and year_range[0] and year_range[1]:
            min_year, max_year = int(year_range[0]), int(year_range[1])
            selected_years = st.slider(
                "Year Range",
                min_value=min_year,
                max_value=max_year,
                value=(max(min_year, max_year - 5), max_year)
            )
        else:
            st.info("No year data available")
            return

    # ────────────────────────────────────────────────────────────────────────
    # Query Funding by Field Over Time
    # ────────────────────────────────────────────────────────────────────────

    source_filter = "" if selected_source == "All" else f"AND source = '{selected_source}'"

    funding_by_field_query = f"""
        SELECT
            award_year,
            program_name,
            COUNT(*) as num_awards,
            SUM(funding_amount) / 1e6 as funding_millions,
            AVG(funding_amount) / 1e3 as avg_award_thousands
        FROM analytics_intermediate.int_all_awards
        WHERE award_year BETWEEN {selected_years[0]} AND {selected_years[1]}
          AND program_name IS NOT NULL
          AND funding_amount IS NOT NULL
          {source_filter}
        GROUP BY award_year, program_name
        ORDER BY award_year, funding_millions DESC
    """

    funding_by_field = conn.execute(funding_by_field_query).df()

    if not funding_by_field.empty:

        # ────────────────────────────────────────────────────────────────────
        # Overall Trend
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📊 Overall Funding Trend")

        overall_trend = funding_by_field.groupby("award_year").agg({
            "num_awards": "sum",
            "funding_millions": "sum"
        }).reset_index()

        fig = go.Figure()
        fig.add_trace(go.Bar(
            x=overall_trend["award_year"],
            y=overall_trend["num_awards"],
            name="Number of Awards",
            yaxis="y1",
            marker_color="lightblue"
        ))
        fig.add_trace(go.Scatter(
            x=overall_trend["award_year"],
            y=overall_trend["funding_millions"],
            name="Total Funding ($M)",
            yaxis="y2",
            mode="lines+markers",
            marker=dict(size=8, color="darkgreen"),
            line=dict(width=3)
        ))
        fig.update_layout(
            xaxis_title="Year",
            yaxis=dict(title="Number of Awards", side="left"),
            yaxis2=dict(title="Total Funding ($M)", overlaying="y", side="right"),
            hovermode="x unified",
            height=400
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Top Fields (Current Year)
        # ────────────────────────────────────────────────────────────────────

        st.subheader(f"🏆 Top 10 Fields in {selected_years[1]}")

        current_year_data = funding_by_field[
            funding_by_field["award_year"] == selected_years[1]
        ].sort_values("funding_millions", ascending=False).head(10)

        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                current_year_data,
                x="funding_millions",
                y="program_name",
                orientation="h",
                title=f"Top 10 Fields by Funding ({selected_years[1]})",
                labels={"funding_millions": "Funding ($M)", "program_name": "Field"},
                color="funding_millions",
                color_continuous_scale="Greens"
            )
            fig.update_layout(
                yaxis={"categoryorder": "total ascending"},
                showlegend=False
            )
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            display_df = current_year_data[["program_name", "num_awards", "funding_millions"]].copy()
            display_df["funding_millions"] = display_df["funding_millions"].apply(lambda x: f"${x:.1f}M")
            display_df.columns = ["Field", "Awards", "Funding"]
            st.dataframe(display_df, use_container_width=True, hide_index=True, height=400)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Growth Analysis (Year-over-Year)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📈 Year-over-Year Growth Analysis")

        # Calculate YoY growth for fields with data in both years
        growth_query = f"""
            WITH current_year AS (
                SELECT
                    program_name,
                    SUM(funding_amount) as current_funding
                FROM analytics_intermediate.int_all_awards
                WHERE award_year = {selected_years[1]}
                  AND program_name IS NOT NULL
                  {source_filter}
                GROUP BY program_name
            ),
            previous_year AS (
                SELECT
                    program_name,
                    SUM(funding_amount) as previous_funding
                FROM analytics_intermediate.int_all_awards
                WHERE award_year = {selected_years[1] - 1}
                  AND program_name IS NOT NULL
                  {source_filter}
                GROUP BY program_name
            )
            SELECT
                c.program_name,
                p.previous_funding / 1e6 as previous_funding_millions,
                c.current_funding / 1e6 as current_funding_millions,
                100.0 * (c.current_funding - p.previous_funding) / p.previous_funding as growth_pct
            FROM current_year c
            INNER JOIN previous_year p ON c.program_name = p.program_name
            WHERE p.previous_funding > 1e6  -- Only fields with >$1M previous funding
            ORDER BY growth_pct DESC
        """

        growth_data = conn.execute(growth_query).df()

        if not growth_data.empty and len(growth_data) > 0:
            col1, col2 = st.columns(2)

            with col1:
                st.markdown(f"**🔥 Fastest Growing (Top 10)**")
                top_growth = growth_data.head(10)
                top_growth_display = top_growth[[
                    "program_name", "previous_funding_millions",
                    "current_funding_millions", "growth_pct"
                ]].copy()
                top_growth_display["growth_pct"] = top_growth_display["growth_pct"].apply(
                    lambda x: f"+{x:.1f}%" if x > 0 else f"{x:.1f}%"
                )
                top_growth_display["previous_funding_millions"] = top_growth_display[
                    "previous_funding_millions"
                ].apply(lambda x: f"${x:.1f}M")
                top_growth_display["current_funding_millions"] = top_growth_display[
                    "current_funding_millions"
                ].apply(lambda x: f"${x:.1f}M")
                top_growth_display.columns = ["Field", f"{selected_years[1]-1}", f"{selected_years[1]}", "Growth"]
                st.dataframe(top_growth_display, use_container_width=True, hide_index=True)

            with col2:
                st.markdown(f"**❄️ Fastest Declining (Bottom 10)**")
                bottom_growth = growth_data.tail(10).sort_values("growth_pct")
                bottom_growth_display = bottom_growth[[
                    "program_name", "previous_funding_millions",
                    "current_funding_millions", "growth_pct"
                ]].copy()
                bottom_growth_display["growth_pct"] = bottom_growth_display["growth_pct"].apply(
                    lambda x: f"+{x:.1f}%" if x > 0 else f"{x:.1f}%"
                )
                bottom_growth_display["previous_funding_millions"] = bottom_growth_display[
                    "previous_funding_millions"
                ].apply(lambda x: f"${x:.1f}M")
                bottom_growth_display["current_funding_millions"] = bottom_growth_display[
                    "current_funding_millions"
                ].apply(lambda x: f"${x:.1f}M")
                bottom_growth_display.columns = ["Field", f"{selected_years[1]-1}", f"{selected_years[1]}", "Growth"]
                st.dataframe(bottom_growth_display, use_container_width=True, hide_index=True)

            # Growth distribution
            st.markdown("**Distribution of YoY Growth Rates**")
            fig = px.histogram(
                growth_data,
                x="growth_pct",
                nbins=30,
                title="Distribution of Year-over-Year Growth",
                labels={"growth_pct": "YoY Growth (%)"},
                color_discrete_sequence=["steelblue"]
            )
            fig.add_vline(x=0, line_dash="dash", line_color="red", annotation_text="No Growth")
            st.plotly_chart(fig, use_container_width=True)

        else:
            st.info("Not enough data for year-over-year comparison. Need at least 2 consecutive years.")

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Field Comparison (Time Series)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🔬 Compare Specific Fields Over Time")

        # Get top fields for selection
        top_fields = funding_by_field.groupby("program_name")["funding_millions"].sum().sort_values(
            ascending=False
        ).head(20).index.tolist()

        selected_fields = st.multiselect(
            "Select fields to compare (up to 5)",
            options=top_fields,
            default=top_fields[:3] if len(top_fields) >= 3 else top_fields,
            max_selections=5
        )

        if selected_fields:
            field_comparison = funding_by_field[
                funding_by_field["program_name"].isin(selected_fields)
            ]

            fig = px.line(
                field_comparison,
                x="award_year",
                y="funding_millions",
                color="program_name",
                markers=True,
                title="Funding Trends for Selected Fields",
                labels={
                    "award_year": "Year",
                    "funding_millions": "Funding ($M)",
                    "program_name": "Field"
                }
            )
            fig.update_layout(hovermode="x unified", height=500)
            st.plotly_chart(fig, use_container_width=True)

            # Awards count comparison
            fig2 = px.line(
                field_comparison,
                x="award_year",
                y="num_awards",
                color="program_name",
                markers=True,
                title="Number of Awards for Selected Fields",
                labels={
                    "award_year": "Year",
                    "num_awards": "Number of Awards",
                    "program_name": "Field"
                }
            )
            fig2.update_layout(hovermode="x unified", height=500)
            st.plotly_chart(fig2, use_container_width=True)

    else:
        st.info("No funding data available for selected filters.")

    # ────────────────────────────────────────────────────────────────────────
    # Interpretation Guide
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    with st.expander("💡 How to Interpret These Trends"):
        st.markdown("""
        **Growing Fields (>20% YoY):**
        - Emerging federal priorities
        - More funding competition but also more opportunities
        - Good for early-career researchers building new expertise

        **Stable Fields (-5% to +5% YoY):**
        - Mature research areas with consistent funding
        - Safer bet for established researchers
        - Predictable opportunity landscape

        **Declining Fields (<-20% YoY):**
        - Reduced federal priority or funding
        - Fewer new positions, more competition for remaining spots
        - Consider pivoting to adjacent growing fields

        **What Drives Trends:**
        - Policy changes (e.g., NIH focus on cancer research)
        - Technological breakthroughs (e.g., CRISPR → biotech boom)
        - Global events (e.g., pandemic → virology surge)
        - Economic conditions (recessions → applied research emphasis)

        **Strategy:**
        - Align your research with 2-3 growing fields
        - Develop transferable skills for field-switching
        - Monitor trends quarterly to spot early signals
        """)
