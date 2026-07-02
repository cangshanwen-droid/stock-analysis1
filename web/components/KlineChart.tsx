"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time
} from "lightweight-charts";
import type { Candle } from "../lib/types";

type Props = {
  candles: Candle[];
};

export function KlineChart({ candles }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma10Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const roundLabelRef = useRef<Map<string, number>>(new Map());

  const candleData = useMemo(() => candles.map(({ time, open, high, low, close }) => ({
    time,
    open,
    high,
    low,
    close
  })), [candles]);

  const ma5Data = useMemo(() => candles.map((candle, index) => ({
    time: candle.time,
    value: Number((candles.slice(Math.max(0, index - 4), index + 1).reduce((sum, item) => sum + item.close, 0) / Math.min(5, index + 1)).toFixed(2))
  })), [candles]);

  const ma10Data = useMemo(() => candles.map((candle, index) => ({
    time: candle.time,
    value: Number((candles.slice(Math.max(0, index - 9), index + 1).reduce((sum, item) => sum + item.close, 0) / Math.min(10, index + 1)).toFixed(2))
  })), [candles]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1220" },
        textColor: "#94a3b8",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "#162235" },
        horzLines: { color: "#1e2a3a" }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#64748b", width: 1, labelBackgroundColor: "#334155" },
        horzLine: { color: "#64748b", width: 1, labelBackgroundColor: "#334155" }
      },
      rightPriceScale: {
        borderColor: "#1e2a3a",
        scaleMargins: { top: 0.08, bottom: 0.22 }
      },
      timeScale: {
        borderColor: "#1e2a3a",
        rightOffset: 8,
        barSpacing: 10,
        tickMarkFormatter: (time: Time) => {
          const key = String(time);
          const round = roundLabelRef.current.get(key);
          return round ? `第${round}轮` : "";
        }
      },
      handleScale: true,
      handleScroll: true
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "rgba(0,0,0,0)",
      downColor: "#089981",
      borderUpColor: "#f23645",
      borderDownColor: "#089981",
      wickUpColor: "#f23645",
      wickDownColor: "#089981",
      priceLineColor: "#fbbf24"
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "rgba(148, 163, 184, 0.32)"
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.78, bottom: 0 }
    });

    const ma5Series = chart.addLineSeries({
      color: "#eab308",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const ma10Series = chart.addLineSeries({
      color: "#60a5fa",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volumeSeries;
    ma5Ref.current = ma5Series;
    ma10Ref.current = ma10Series;

    const resize = () => {
      if (!ref.current) return;
      chart.applyOptions({ width: ref.current.clientWidth, height: ref.current.clientHeight });
    };
    window.addEventListener("resize", resize);
    return () => {
      window.removeEventListener("resize", resize);
      chart.remove();
      chartRef.current = null;
      candleRef.current = null;
      volumeRef.current = null;
      ma5Ref.current = null;
      ma10Ref.current = null;
    };
  }, []);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !ma5Ref.current || !ma10Ref.current || !chartRef.current) return;
    roundLabelRef.current = new Map(candles.map((candle) => [String(candle.time), candle.round]));
    candleRef.current.setData(candleData);
    volumeRef.current.setData(candles.map((c) => ({
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(242,54,69,.36)" : "rgba(8,153,129,.36)"
    })));
    ma5Ref.current.setData(ma5Data);
    ma10Ref.current.setData(ma10Data);
    if (candles.length <= 12) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: -6,
        to: Math.max(18, candles.length + 8)
      });
    } else {
      chartRef.current.timeScale().fitContent();
    }
  }, [candleData, candles, ma5Data, ma10Data]);

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-ma5">MA5</span>
        <span className="legend-ma10">MA10</span>
        <span>成交量</span>
      </div>
      <div className="chart-host" ref={ref} />
    </div>
  );
}
