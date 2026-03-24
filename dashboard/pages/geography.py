"""
Geography Page

BQ-6: How does funding vary by state/province?

Analyzes geographic distribution of research funding to identify regional
research hubs and funding patterns.
"""

import streamlit as st
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd


def show(conn):
    """Render the Geography page."""

    st.title("🗺️ Geographic Analysis")

    st.markdown("""
    **Explore research funding distribution across North America.**

    Some states/provinces are research powerhouses. Others are emerging.
    Geographic location affects funding opportunities, cost of living, and career networks.
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
        # Metric to display
        metric = st.selectbox(
            "Display metric",
            ["Total Funding", "Number of Awards", "Avg Award Size", "Unique Institutions"]
        )

    # ────────────────────────────────────────────────────────────────────────
    # Query Geographic Data
    # ────────────────────────────────────────────────────────────────────────

    source_filter = "" if selected_source == "All" else f"AND source = '{selected_source}'"

    # Geographic data by state
    geo_query = f"""
        SELECT
            state,
            COUNT(*) as num_awards,
            COUNT(DISTINCT institution) as num_institutions,
            SUM(funding_amount) / 1e6 as total_funding_millions,
            AVG(funding_amount) / 1e3 as avg_award_thousands
        FROM analytics_intermediate.int_all_awards
        WHERE state IS NOT NULL
          AND funding_amount IS NOT NULL
          {source_filter}
        GROUP BY state
        ORDER BY total_funding_millions DESC
    """

    geo_data = conn.execute(geo_query).df()

    if not geo_data.empty:

        # ────────────────────────────────────────────────────────────────────
        # Summary Metrics
        # ────────────────────────────────────────────────────────────────────

        col1, col2, col3, col4 = st.columns(4)

        num_states = len(geo_data)
        col1.metric("States/Provinces", num_states)

        total_funding = geo_data["total_funding_millions"].sum()
        col2.metric("Total Funding", f"${total_funding:.1f}M")

        total_institutions = geo_data["num_institutions"].sum()
        col3.metric("Total Institutions", f"{total_institutions:,}")

        top_state = geo_data.iloc[0]
        col4.metric(
            f"Top State ({top_state['state']})",
            f"${top_state['total_funding_millions']:.1f}M"
        )

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Choropleth Map (US States)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🗺️ Funding Distribution Map")

        # Prepare data for map
        metric_mapping = {
            "Total Funding": ("total_funding_millions", "Total Funding ($M)"),
            "Number of Awards": ("num_awards", "Number of Awards"),
            "Avg Award Size": ("avg_award_thousands", "Avg Award Size ($K)"),
            "Unique Institutions": ("num_institutions", "Number of Institutions")
        }
        metric_col, metric_label = metric_mapping[metric]

        # Create choropleth map for US states
        fig = px.choropleth(
            geo_data,
            locations="state",
            locationmode="USA-states",
            color=metric_col,
            scope="usa",
            title=f"{metric} by State",
            labels={metric_col: metric_label},
            color_continuous_scale="Viridis",
            hover_data={
                "state": True,
                "num_awards": True,
                "total_funding_millions": ":.1f",
                "avg_award_thousands": ":.0f",
                "num_institutions": True
            }
        )
        fig.update_layout(
            geo=dict(bgcolor="rgba(0,0,0,0)", lakecolor="lightblue"),
            height=500
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Top States Ranking
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🏆 Top 15 States/Provinces by Funding")

        top_states = geo_data.head(15)

        col1, col2 = st.columns([2, 1])

        with col1:
            fig = px.bar(
                top_states,
                x="total_funding_millions",
                y="state",
                orientation="h",
                title="Top 15 States by Total Funding",
                labels={"total_funding_millions": "Total Funding ($M)", "state": "State"},
                color="total_funding_millions",
                color_continuous_scale="Blues"
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False)
            st.plotly_chart(fig, use_container_width=True)

        with col2:
            display_df = top_states[["state", "num_awards", "num_institutions", "total_funding_millions"]].copy()
            display_df["total_funding_millions"] = display_df["total_funding_millions"].apply(
                lambda x: f"${x:.1f}M"
            )
            display_df.columns = ["State", "Awards", "Institutions", "Funding"]
            st.dataframe(display_df, use_container_width=True, hide_index=True, height=400)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Funding Concentration Analysis
        # ────────────────────────────────────────────────────────────────────

        st.subheader("📊 Regional Concentration")

        # Calculate percentages
        geo_data["funding_pct"] = 100 * geo_data["total_funding_millions"] / geo_data["total_funding_millions"].sum()
        geo_data["awards_pct"] = 100 * geo_data["num_awards"] / geo_data["num_awards"].sum()

        # Top states concentration
        top_5_funding_pct = geo_data.head(5)["funding_pct"].sum()
        top_10_funding_pct = geo_data.head(10)["funding_pct"].sum()

        col1, col2, col3 = st.columns(3)
        col1.metric("Top 5 states capture", f"{top_5_funding_pct:.1f}%")
        col2.metric("Top 10 states capture", f"{top_10_funding_pct:.1f}%")
        col3.metric("States with <1% share", len(geo_data[geo_data["funding_pct"] < 1]))

        # Pie chart of top states
        top_10_states = geo_data.head(10).copy()
        others_funding = geo_data.iloc[10:]["total_funding_millions"].sum()
        if others_funding > 0:
            others_row = pd.DataFrame({
                "state": ["Others"],
                "total_funding_millions": [others_funding]
            })
            pie_data = pd.concat([top_10_states[["state", "total_funding_millions"]], others_row], ignore_index=True)
        else:
            pie_data = top_10_states[["state", "total_funding_millions"]]

        fig = px.pie(
            pie_data,
            names="state",
            values="total_funding_millions",
            title="Funding Distribution: Top 10 States vs Others",
            hole=0.3
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Funding Efficiency (Funding per Institution)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("💡 Funding Efficiency — Funding per Institution")

        geo_data["funding_per_institution"] = geo_data["total_funding_millions"] / geo_data["num_institutions"]

        # Filter to states with at least 3 institutions for fair comparison
        efficiency_data = geo_data[geo_data["num_institutions"] >= 3].copy()
        efficiency_data = efficiency_data.sort_values("funding_per_institution", ascending=False).head(15)

        fig = px.bar(
            efficiency_data,
            x="funding_per_institution",
            y="state",
            orientation="h",
            title="Top 15 States by Funding per Institution (min 3 institutions)",
            labels={"funding_per_institution": "Funding per Institution ($M)", "state": "State"},
            color="funding_per_institution",
            color_continuous_scale="Greens"
        )
        fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False)
        st.plotly_chart(fig, use_container_width=True)

        st.caption("States with high funding per institution indicate concentrated research excellence.")

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # Scatter: Funding vs Cost of Living Proxy
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🔬 Funding vs Research Density")

        fig = px.scatter(
            geo_data,
            x="num_institutions",
            y="total_funding_millions",
            size="num_awards",
            hover_name="state",
            hover_data={
                "num_awards": True,
                "total_funding_millions": ":.1f",
                "num_institutions": True,
                "avg_award_thousands": ":.0f"
            },
            labels={
                "num_institutions": "Number of Institutions",
                "total_funding_millions": "Total Funding ($M)",
                "num_awards": "Number of Awards"
            },
            title="State Funding vs Research Density",
            color="avg_award_thousands",
            color_continuous_scale="Plasma"
        )
        st.plotly_chart(fig, use_container_width=True)

        st.markdown("---")

        # ────────────────────────────────────────────────────────────────────
        # City-Level Analysis (Top Cities)
        # ────────────────────────────────────────────────────────────────────

        st.subheader("🏙️ Top Research Cities")

        city_query = f"""
            SELECT
                city,
                state,
                COUNT(*) as num_awards,
                COUNT(DISTINCT institution) as num_institutions,
                SUM(funding_amount) / 1e6 as total_funding_millions
            FROM analytics_intermediate.int_all_awards
            WHERE city IS NOT NULL
              AND state IS NOT NULL
              AND funding_amount IS NOT NULL
              {source_filter}
            GROUP BY city, state
            ORDER BY total_funding_millions DESC
            LIMIT 20
        """

        city_data = conn.execute(city_query).df()

        if not city_data.empty:
            city_data["location"] = city_data["city"] + ", " + city_data["state"]

            fig = px.bar(
                city_data,
                x="total_funding_millions",
                y="location",
                orientation="h",
                title="Top 20 Research Cities",
                labels={"total_funding_millions": "Total Funding ($M)", "location": "City"},
                color="total_funding_millions",
                color_continuous_scale="Oranges"
            )
            fig.update_layout(yaxis={"categoryorder": "total ascending"}, showlegend=False, height=600)
            st.plotly_chart(fig, use_container_width=True)

        # ────────────────────────────────────────────────────────────────────
        # Download Data
        # ────────────────────────────────────────────────────────────────────

        st.markdown("---")
        csv_data = geo_data.to_csv(index=False)
        st.download_button(
            label="📥 Download geographic data as CSV",
            data=csv_data,
            file_name=f"geographic_funding_{selected_source.lower()}.csv",
            mime="text/csv"
        )

    else:
        st.info("No geographic data available for selected filters.")

    # ────────────────────────────────────────────────────────────────────────
    # Geographic Strategy Guide
    # ────────────────────────────────────────────────────────────────────────

    st.markdown("---")
    with st.expander("💡 How to Use Geographic Intelligence"):
        st.markdown("""
        **High-Funding States (CA, MA, NY, TX):**
        - **Pros:** More opportunities, strong research networks, industry connections
        - **Cons:** High cost of living, intense competition
        - **Best for:** Ambitious researchers prioritizing prestige and connections

        **Mid-Tier States (IL, PA, MD, WA):**
        - **Pros:** Good funding, lower cost of living, growing research hubs
        - **Cons:** Fewer opportunities than top states
        - **Best for:** Balanced quality-of-life and research quality

        **Emerging States (AZ, CO, NC, GA):**
        - **Pros:** Growing research investment, affordable living, less competition
        - **Cons:** Fewer established programs, smaller networks
        - **Best for:** Early-career researchers willing to build new connections

        **Funding per Institution Metric:**
        - High value = Concentrated research excellence (e.g., MIT in MA)
        - Low value = Spread across many mid-tier institutions
        - Consider both absolute funding and efficiency

        **Location Strategy:**
        - **Priority 1:** Find the right PI fit (more important than state)
        - **Priority 2:** Consider cost of living vs stipend
        - **Priority 3:** Proximity to industry (tech → CA/WA, biotech → MA/CA, energy → TX)
        - **Priority 4:** Quality of life (weather, culture, family considerations)

        **International Students:**
        - High-funding states often have better visa/immigration support
        - Cities with large international populations = easier transition
        - Consider timezone for family communication
        """)
