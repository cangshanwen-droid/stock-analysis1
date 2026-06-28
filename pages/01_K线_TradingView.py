"""
双镜 · TradingView 风格专业K线页面
基于 streamlit-lightweight-charts-v5 实现
依赖: pip install streamlit-lightweight-charts-v5 yfinance pandas
"""
import streamlit as st
import yfinance as yf
import pandas as pd
from lightweight_charts_v5 import lightweight_charts_v5_component

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 配色方案 — 深色专业主题
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
COLORS = {
    "bg": "#0e1219",           # 最底层背景
    "chart_bg": "#131920",     # 图表区背景
    "crosshair": "#5e6b7e",    # 十字光标
    "grid_h": "#2a3340",       # 水平网格线
    "grid_v": "#1e2630",       # 垂直网格线
    "text": "#8a9bb5",         # 文字
    "text_title": "#d1d8e6",   # 标题文字
    "up": "#ef5350",           # 涨 - 红
    "down": "#2ecc71",         # 跌 - 绿
    "vol_up": "rgba(239,83,80,0.4)",
    "vol_down": "rgba(46,204,113,0.4)",
    "ma5": "#f59e0b",
    "ma10": "#a78bfa",
    "ma20": "#60a5fa",
    "watermark": "#2a3340",
}

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 数据获取
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
@st.cache_data(ttl=3600, show_spinner="下载行情数据...")
def fetch_kline(ticker: str, period: str = "6mo") -> pd.DataFrame:
    """从 yfinance 获取股票日线数据"""
    df = yf.download(ticker, period=period, interval="1d", auto_adjust=True)
    if df.empty:
        return df
    # 展平 MultiIndex（如果有）
    df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    df = df.dropna(subset=["Open", "High", "Low", "Close", "Volume"])
    return df

def to_chart_data(df: pd.DataFrame) -> list:
    """转换为 Lightweight Charts 标准格式"""
    return [
        {
            "time": str(date.date()),
            "open": float(row["Open"]),
            "high": float(row["High"]),
            "low": float(row["Low"]),
            "close": float(row["Close"]),
        }
        for date, row in df.iterrows()
    ]

def to_volume_data(df: pd.DataFrame) -> list:
    """成交量数据，标记涨跌颜色"""
    return [
        {
            "time": str(date.date()),
            "value": float(row["Volume"]),
            "color": COLORS["vol_up"] if row["Close"] >= row["Open"] else COLORS["vol_down"],
        }
        for date, row in df.iterrows()
    ]

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 页面
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
def page_kline_tradingview():
    st.markdown("""
    <style>
        /* 隐藏 Streamlit 默认元素 */
        #MainMenu, .stDeployButton, footer,
        [data-testid="stStatusWidget"], [data-testid="stToolbar"],
        [data-testid="stDecoration"] { display: none !important; }
        .stApp { background: #0e1219; }
        section.main > div.block-container { padding: 8px 16px !important; max-width: 1400px !important; }
        /* 输入框深色主题 */
        div[data-testid="stTextInput"] input {
            background: #1a2230 !important;
            border: 1px solid #2a3340 !important;
            color: #d1d8e6 !important;
            border-radius: 8px !important;
            font-size: 16px !important;
        }
        div[data-testid="stTextInput"] input:focus {
            border-color: #3b82f6 !important;
            box-shadow: 0 0 0 2px rgba(59,130,246,0.2) !important;
        }
        div[data-testid="stTextInput"] label {
            color: #8a9bb5 !important;
        }
        /* 指标卡片 */
        .metric-row {
            display: flex; gap: 12px; margin: 8px 0 16px 0;
        }
        .metric-card {
            background: #131920; border: 1px solid #2a3340;
            border-radius: 8px; padding: 10px 16px; flex: 1;
        }
        .metric-card .label {
            font-size: 11px; color: #5e6b7e; text-transform: uppercase;
            letter-spacing: 1px; margin-bottom: 4px;
        }
        .metric-card .value {
            font-size: 22px; font-weight: 700;
            font-feature-settings: "tnum"; font-variant-numeric: tabular-nums;
        }
        .metric-card .value.up { color: #ef5350; }
        .metric-card .value.down { color: #2ecc71; }
        .metric-card .sub {
            font-size: 12px; color: #8a9bb5; margin-top: 2px;
        }
    </style>
    """, unsafe_allow_html=True)

    # ── 顶栏 ──
    c1, c2, c3 = st.columns([2, 3, 1])
    with c1:
        st.markdown(
            '<span style="font-size:26px;font-weight:800;letter-spacing:4px;'
            'background:linear-gradient(135deg,#f0e6d3,#d4a853);'
            '-webkit-background-clip:text;-webkit-text-fill-color:transparent;'
            'background-clip:text;">双镜 · K线</span>',
            unsafe_allow_html=True,
        )
    with c2:
        ticker = st.text_input(
            "股票代码",
            value="AAPL",
            max_chars=10,
            label_visibility="collapsed",
            placeholder="输入股票代码，如 AAPL、TSLA、600519.SS",
        ).strip().upper()
    with c3:
        period = st.selectbox(
            "周期",
            options=["1mo", "3mo", "6mo", "1y", "2y"],
            index=3,
            label_visibility="collapsed",
        )

    if not ticker:
        st.info("请输入股票代码")
        return

    # ── 获取数据 ──
    with st.spinner(f"正在获取 {ticker} 行情数据..."):
        df = fetch_kline(ticker, period)

    if df.empty or len(df) < 5:
        st.error(f"无法获取 {ticker} 的数据，请检查代码是否正确")
        return

    # ── 最新行情指标 ──
    last = df.iloc[-1]
    prev = df.iloc[-2]
    change = float(last["Close"] - prev["Close"])
    change_pct = change / float(prev["Close"]) * 100
    high_52w = float(df["High"].max())
    low_52w = float(df["Low"].min())
    avg_vol = int(df["Volume"].tail(20).mean())

    cls = "up" if change >= 0 else "down"
    st.markdown(
        f'<div class="metric-row">'
        f'<div class="metric-card"><div class="label">最新价</div>'
        f'<div class="value {cls}">${float(last["Close"]):.2f}</div>'
        f'<div class="sub">{change:+.2f} ({change_pct:+.2f}%)</div></div>'
        f'<div class="metric-card"><div class="label">最高</div>'
        f'<div class="value">{float(last["High"]):.2f}</div>'
        f'<div class="sub">52周最高 {high_52w:.2f}</div></div>'
        f'<div class="metric-card"><div class="label">最低</div>'
        f'<div class="value">{float(last["Low"]):.2f}</div>'
        f'<div class="sub">52周最低 {low_52w:.2f}</div></div>'
        f'<div class="metric-card"><div class="label">成交量</div>'
        f'<div class="value">{int(last["Volume"]):,}</div>'
        f'<div class="sub">20日均量 {avg_vol:,}</div></div>'
        f'</div>',
        unsafe_allow_html=True,
    )

    # ── 转换数据 ──
    chart_data = to_chart_data(df)
    volume_data = to_volume_data(df)

    # ── 均线计算 ──
    def calc_ma(df: pd.DataFrame, period: int) -> list:
        ma = df["Close"].rolling(period).mean()
        return [
            {"time": str(date.date()), "value": round(float(v), 2)}
            for date, v in ma.dropna().items()
        ]

    ma5 = calc_ma(df, 5)
    ma10 = calc_ma(df, 10)
    ma20 = calc_ma(df, 20)

    # ── 构建图表配置 ──
    chart_options = {
        "layout": {
            "background": {"color": COLORS["chart_bg"]},
            "textColor": COLORS["text"],
            "fontFamily": "-apple-system, BlinkMacSystemFont, 'PingFang SC', 'Microsoft YaHei', sans-serif",
            "fontSize": 11,
        },
        "grid": {
            "vertLines": {"color": COLORS["grid_v"], "style": 1},
            "horzLines": {"color": COLORS["grid_h"], "style": 1},
        },
        "crosshair": {
            "mode": 0,
            "vertLine": {
                "color": COLORS["crosshair"],
                "width": 1,
                "style": 2,
                "labelBackgroundColor": COLORS["crosshair"],
            },
            "horzLine": {
                "color": COLORS["crosshair"],
                "width": 1,
                "style": 2,
                "labelBackgroundColor": COLORS["crosshair"],
            },
        },
        "rightPriceScale": {
            "borderColor": COLORS["grid_v"],
            "scaleMargins": {"top": 0.05, "bottom": 0.05},
        },
        "timeScale": {
            "borderColor": COLORS["grid_v"],
            "timeVisible": False,
            "secondsVisible": False,
        },
        "watermark": {
            "visible": True,
            "text": f"{ticker} · 双镜",
            "color": COLORS["watermark"],
            "fontSize": 48,
            "fontFamily": "monospace",
        },
    }

    # K线系列
    candlestick_series = {
        "type": "Candlestick",
        "data": chart_data,
        "options": {
            "upColor": COLORS["up"],
            "downColor": COLORS["down"],
            "borderUpColor": COLORS["up"],
            "borderDownColor": COLORS["down"],
            "wickUpColor": COLORS["up"],
            "wickDownColor": COLORS["down"],
        },
    }

    # 均线系列
    ma_series = []
    for ma_data, color, name in [
        (ma5, COLORS["ma5"], "MA5"),
        (ma10, COLORS["ma10"], "MA10"),
        (ma20, COLORS["ma20"], "MA20"),
    ]:
        if ma_data:
            ma_series.append({
                "type": "Line",
                "data": ma_data,
                "options": {
                    "color": color,
                    "lineWidth": 1,
                    "priceLineVisible": False,
                    "lastValueVisible": True,
                    "priceFormat": {"type": "price"},
                },
            })

    # 成交量副图
    volume_series = {
        "type": "Histogram",
        "data": volume_data,
        "options": {
            "priceFormat": {"type": "volume"},
            "priceLineVisible": False,
            "lastValueVisible": False,
        },
    }

    # 主图配置
    main_chart = {
        "chart": chart_options,
        "series": [candlestick_series] + ma_series,
        "height": 480,
    }

    # 成交量副图配置
    volume_chart = {
        "chart": {
            "layout": {
                "background": {"color": COLORS["chart_bg"]},
                "textColor": COLORS["text"],
            },
            "grid": {
                "vertLines": {"color": COLORS["grid_v"], "style": 1},
                "horzLines": {"color": COLORS["grid_h"], "style": 1},
            },
            "rightPriceScale": {
                "borderColor": COLORS["grid_v"],
                "scaleMargins": {"top": 0.05, "bottom": 0.05},
                "visible": False,
            },
            "timeScale": {
                "borderColor": COLORS["grid_v"],
                "visible": True,
            },
            "crosshair": {
                "mode": 0,
                "vertLine": {
                    "color": COLORS["crosshair"],
                    "width": 1,
                    "style": 2,
                    "labelBackgroundColor": COLORS["crosshair"],
                },
                "horzLine": {
                    "color": COLORS["crosshair"],
                    "width": 1,
                    "style": 2,
                    "labelBackgroundColor": COLORS["crosshair"],
                },
            },
        },
        "series": [volume_series],
        "height": 140,
    }

    # ── 渲染图表 ──
    result = lightweight_charts_v5_component(
        name=f"{ticker} - 双镜专业K线",
        charts=[main_chart, volume_chart],
        height=640,
        zoom_level=len(chart_data),
        key=f"kline_{ticker}_{period}",
    )

    # ── 数据表格 ──
    with st.expander("📋 历史数据", expanded=False):
        show = df.copy()
        show.columns = [c[0] if isinstance(c, tuple) else c for c in show.columns]
        show.index = show.index.date
        show = show.rename_axis("日期")
        show["涨跌幅"] = show["Close"].pct_change() * 100
        show["涨跌幅"] = show["涨跌幅"].apply(lambda x: f"{x:+.2f}%")
        show["Volume"] = show["Volume"].apply(lambda x: f"{int(x):,}")
        show["Open"] = show["Open"].apply(lambda x: f"{x:.2f}")
        show["High"] = show["High"].apply(lambda x: f"{x:.2f}")
        show["Low"] = show["Low"].apply(lambda x: f"{x:.2f}")
        show["Close"] = show["Close"].apply(lambda x: f"{x:.2f}")
        st.dataframe(show[["Open", "High", "Low", "Close", "Volume", "涨跌幅"]],
                     use_container_width=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 独立运行入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == "__main__":
    st.set_page_config(
        page_title="双镜 · TradingView K线",
        layout="wide",
        initial_sidebar_state="collapsed",
    )
    page_kline_tradingview()
