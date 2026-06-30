"""Insert _render_dash_chart fragment function into app.py"""
with open("app.py", "r", encoding="utf-8") as f:
    content = f.read()

# Use """ for outer, ''' for inner to avoid nesting conflict
fragment = """

@st.fragment
def _render_dash_chart(stocks, sym):
    '''K线图（独立 fragment，切换标签不阻塞页面）'''
    selected_stock = next((s for s in stocks if s["symbol"] == sym), stocks[0])
    data = get_kline_data(sym)
    if not data:
        st.markdown('<div style="color:rgba(255,255,255,.3);text-align:center;padding:30px;">暂无K线数据</div>', unsafe_allow_html=True)
        return
    raw_k = pd.DataFrame(data).sort_values("round").reset_index(drop=True)
    first_open = float(raw_k.iloc[0]["open_price"])
    df_k = build_professional_kline_view(raw_k, sym)
    df_k["x_pos"] = df_k["display_round"]
    df_k["x_label"] = df_k["display_round"].apply(lambda r: f"{int(r)}")
    x_values = df_k["x_pos"]
    RED_UP = "#d64b45"; GREEN_DN = "#07984f"
    up_mask = df_k["close_price"] >= df_k["open_price"]
    body_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
    candle_line = [RED_UP if u else GREEN_DN for u in up_mask]
    vol_fill = ["rgba(255,255,255,0)" if u else GREEN_DN for u in up_mask]
    vol_line = [RED_UP if u else GREEN_DN for u in up_mask]
    fig = make_subplots(rows=2, cols=1, shared_xaxes=True, vertical_spacing=0.03, row_heights=[0.75, 0.25])
    for is_up, color in [(True, RED_UP), (False, GREEN_DN)]:
        wick_x, wick_y = [], []
        for _, r in df_k[up_mask == is_up].iterrows():
            wick_x.extend([r["x_pos"], r["x_pos"], None])
            wick_y.extend([r["low_price"], r["high_price"], None])
        fig.add_trace(go.Scatter(x=wick_x, y=wick_y, mode="lines",
            line=dict(color=color, width=1.05), hoverinfo="skip", showlegend=False), row=1, col=1)
    body_base = df_k[["open_price", "close_price"]].min(axis=1)
    body_height = (df_k["close_price"] - df_k["open_price"]).abs()
    min_body = max((df_k["high_price"].max() - df_k["low_price"].min()) * 0.008, float(df_k.iloc[-1]["close_price"]) * 0.001, 0.01)
    body_height = body_height.where(body_height > min_body, min_body)
    fig.add_trace(go.Bar(x=x_values, y=body_height, base=body_base, width=0.42,
        marker=dict(color=body_fill, line=dict(color=candle_line, width=1.15)),
        name="K线", showlegend=False,
        customdata=np.stack([df_k["display_round"], df_k["source_round"].fillna(0), df_k["open_price"], df_k["high_price"], df_k["low_price"], df_k["close_price"], df_k["change_pct"], df_k["volume"]], axis=-1),
        hovertemplate="轮次 %{customdata[0]}<br>数据轮次 %{customdata[1]:.0f}<br>开盘 %{customdata[2]:,.2f}<br>最高 %{customdata[3]:,.2f}<br>最低 %{customdata[4]:,.2f}<br>收盘 %{customdata[5]:,.2f}<br>涨跌 %{customdata[6]:+.2f}%<br>成交量 %{customdata[7]:,.0f}<extra></extra>"), row=1, col=1)
    for col, color, name in [("upper", "#6b9ec7", "UPPER"), ("mid", "#d6a11d", "MID"), ("lower", "#d957a8", "LOWER")]:
        if df_k[col].notna().any():
            fig.add_trace(go.Scatter(x=x_values, y=df_k[col], mode="lines",
                line=dict(color=color, width=1.25), name=name, hovertemplate=f"{name} %{{y:,.2f}}<extra></extra>"), row=1, col=1)
    for col, color, name in [("ma5", "#f59e0b", "MA5"), ("ma10", "#4c8fbd", "MA10")]:
        if col in df_k and df_k[col].notna().any():
            fig.add_trace(go.Scatter(x=x_values, y=df_k[col], mode="lines",
                line=dict(color=color, width=1.0), name=name, hovertemplate=f"{name} %{{y:,.2f}}<extra></extra>"), row=1, col=1)
    fig.add_trace(go.Bar(x=x_values, y=df_k["volume"], width=0.42,
        marker=dict(color=vol_fill, line=dict(color=vol_line, width=1.05)),
        name="成交量", showlegend=False, hovertemplate="量 %{y:,.0f}<extra></extra>"), row=2, col=1)
    for period, color, name in [(5, "#d6a11d", "VOL5"), (10, "#4c8fbd", "VOL10")]:
        vol_ma = df_k["volume"].rolling(period, min_periods=2).mean()
        fig.add_trace(go.Scatter(x=x_values, y=vol_ma, mode="lines",
            line=dict(color=color, width=1), showlegend=False, hovertemplate=f"{name} %{{y:,.0f}}<extra></extra>"), row=2, col=1)
    latest_mid = float(df_k["mid"].dropna().iloc[-1]) if df_k["mid"].notna().any() else float(df_k.iloc[-1]["close_price"])
    latest_upper = float(df_k["upper"].dropna().iloc[-1]) if df_k["upper"].notna().any() else float(df_k["high_price"].max())
    latest_lower = float(df_k["lower"].dropna().iloc[-1]) if df_k["lower"].notna().any() else float(df_k["low_price"].min())
    st.markdown(('''<div class=\"dash-chart-head\">'
        '<div>'
        '<div class=\"name\">' + esc(selected_stock[\"name\"]) + ' \\u00b7 ' + esc(selected_stock[\"symbol\"]) + '</div>'
        '<div class=\"meta\">BOLL [20,2] \\u2502 MID ' + str(round(latest_mid,2)) + ' \\u2502 UPPER ' + str(round(latest_upper,2)) + ' \\u2502 LOWER ' + str(round(latest_lower,2)) + '</div>'
        '</div>'
        '<div class=\"meta\">\\u7ea2\\u6da8\\u7eff\\u8dcc \\u00b7 BOLL/MA</div>'
        '</div>'), unsafe_allow_html=True)
    tick_step = max(1, len(df_k)//6)
    tick_vals = x_values.iloc[::tick_step]
    tick_text = df_k["x_label"].iloc[::tick_step]
    y_range, _, _, _ = kline_display_range(df_k, first_open)
    y_ticks = np.linspace(y_range[0], y_range[1], 6)
    pct_text = [f"{((v - first_open) / first_open * 100):+.2f}%" if first_open else "0.00%" for v in y_ticks]
    vol_max = float(df_k["volume"].max() or 1)
    vol_ticks = np.linspace(0, vol_max, 4)
    fig.update_layout(height=600, plot_bgcolor="#0b1220", paper_bgcolor="#0b1220",
        margin=dict(t=24, b=8, l=56, r=56), xaxis_rangeslider_visible=False,
        font=dict(color="#94a3b8", size=10), hovermode="x unified",
        hoverlabel=dict(bgcolor="#111827", font_size=12, font_color="#e5e7eb", bordercolor="#334155"),
        xaxis=dict(type="linear", showspikes=True, spikemode="across", spikethickness=0.8, spikecolor="#64748b", spikedash="solid"),
        yaxis=dict(showspikes=True, spikethickness=0.8, spikecolor="#64748b", spikedash="solid"),
        showlegend=True, legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0,
            font=dict(color="#cbd5e1", size=10)),
    )
    fig.update_yaxes(showgrid=True, gridcolor="#1e2a3a", griddash="dot",
        range=y_range, tickmode="array", tickvals=y_ticks, ticktext=[fmt_axis_num(v) for v in y_ticks],
        side="right", row=1, col=1, zeroline=False, tickfont=dict(size=12, color="#94a3b8", family="monospace"))
    fig.update_layout(yaxis3=dict(overlaying="y", anchor="x", side="left", range=y_range,
        tickmode="array", tickvals=y_ticks, ticktext=pct_text,
        showgrid=False, zeroline=False, ticks="outside",
        tickfont=dict(size=12, color="#94a3b8", family="monospace"),
        title=dict(text="", font=dict(size=10, color="#94a3b8")),
    ))
    fig.update_xaxes(showgrid=False, type="linear", tickmode="array", tickvals=tick_vals, ticktext=tick_text, row=1, col=1)
    fig.update_yaxes(showgrid=True, gridcolor="#1e2a3a", griddash="dot",
        tickmode="array", tickvals=vol_ticks, ticktext=[fmt_axis_num(v) for v in vol_ticks],
        side="right", row=2, col=1, zeroline=False, tickfont=dict(size=9, color="#94a3b8"))
    fig.update_xaxes(showgrid=False, type="linear", tickmode="array", tickvals=tick_vals, ticktext=tick_text, row=2, col=1)
    st.markdown('<div class="chart-panel pro-chart">', unsafe_allow_html=True)
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False, "scrollZoom": True})
    st.markdown('</div>', unsafe_allow_html=True)
"""

insert_point = content.find("def page_public_dashboard():")
content = content[:insert_point] + fragment + content[insert_point:]

with open("app.py", "w", encoding="utf-8") as f:
    f.write(content)

print("OK")
