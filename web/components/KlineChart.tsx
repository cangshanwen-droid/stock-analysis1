"use client";

import { useEffect, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  type IChartApi,
  type ISeriesApi
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

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1220" },
        textColor: "#94a3b8",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif"
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
        rightOffset: 4,
        barSpacing: 8
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

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volumeSeries;

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
    };
  }, []);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !chartRef.current) return;
    candleRef.current.setData(candles.map(({ time, open, high, low, close }) => ({
      time,
      open,
      high,
      low,
      close
    })));
    volumeRef.current.setData(candles.map((c) => ({
      time: c.time,
      value: c.volume,
      color: c.close >= c.open ? "rgba(242,54,69,.36)" : "rgba(8,153,129,.36)"
    })));
    chartRef.current.timeScale().fitContent();
  }, [candles]);

  return <div className="chart-host" ref={ref} />;
}
