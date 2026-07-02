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

type DisplayCandle = {
  round: number;
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  isAnchor: boolean;
};

const DISPLAY_START_TIME = 946684800;
const DISPLAY_STEP_SECONDS = 21600;

function round2(value: number) {
  return Number(value.toFixed(2));
}

function expandCandles(candles: Candle[]): DisplayCandle[] {
  return candles.map((candle, index) => ({
    round: candle.round,
    time: (DISPLAY_START_TIME + index * DISPLAY_STEP_SECONDS) as Time,
    open: round2(candle.open),
    high: round2(Math.max(
      candle.high,
      Math.max(candle.open, candle.close) + Math.max(Math.abs(candle.close - candle.open) * 0.18, candle.close * 0.0025, 0.04)
    )),
    low: round2(Math.max(0.01, Math.min(
      candle.low,
      Math.min(candle.open, candle.close) - Math.max(Math.abs(candle.close - candle.open) * 0.14, candle.close * 0.002, 0.03)
    ))),
    close: round2(candle.close),
    volume: Math.max(0, Math.round(candle.volume || 0)),
    isAnchor: true
  }));
}

function movingAverage(candles: DisplayCandle[], windowSize: number) {
  return candles.map((candle, index) => ({
    time: candle.time,
    value: round2(
      candles
        .slice(Math.max(0, index - windowSize + 1), index + 1)
        .reduce((sum, item) => sum + item.close, 0) / Math.min(windowSize, index + 1)
    )
  }));
}

export function KlineChart({ candles }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma10Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const roundLabelRef = useRef<Map<string, number>>(new Map());

  const displayCandles = useMemo(() => expandCandles(candles), [candles]);
  const candleData = useMemo(() => displayCandles.map(({ time, open, high, low, close }) => ({
    time,
    open,
    high,
    low,
    close
  })), [displayCandles]);
  const ma5Data = useMemo(() => movingAverage(displayCandles, 5), [displayCandles]);
  const ma10Data = useMemo(() => movingAverage(displayCandles, 10), [displayCandles]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#0b1220" },
        textColor: "#8ea0b8",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        attributionLogo: false
      },
      grid: {
        vertLines: { color: "rgba(57, 72, 96, 0.36)" },
        horzLines: { color: "rgba(57, 72, 96, 0.46)" }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#7890ad", width: 1, labelBackgroundColor: "#1f2b3d" },
        horzLine: { color: "#7890ad", width: 1, labelBackgroundColor: "#1f2b3d" }
      },
      rightPriceScale: {
        borderColor: "#263448",
        scaleMargins: { top: 0.08, bottom: 0.24 }
      },
      timeScale: {
        borderColor: "#263448",
        rightOffset: 10,
        barSpacing: 24,
        minBarSpacing: 10,
        fixLeftEdge: true,
        fixRightEdge: false,
        tickMarkFormatter: (time: Time) => {
          const round = roundLabelRef.current.get(String(time));
          return round ? `\u7b2c${round}\u8f6e` : "";
        }
      },
      handleScale: true,
      handleScroll: true
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "rgba(242,54,69,0.16)",
      downColor: "#089981",
      borderUpColor: "#f23645",
      borderDownColor: "#089981",
      wickUpColor: "#f23645",
      wickDownColor: "#089981",
      priceLineColor: "#fbbf24",
      priceLineWidth: 1,
      lastValueVisible: true
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "rgba(148, 163, 184, 0.28)",
      lastValueVisible: false,
      priceLineVisible: false
    });

    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.8, bottom: 0 }
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
    roundLabelRef.current = new Map(displayCandles.filter((candle) => candle.isAnchor).map((candle) => [String(candle.time), candle.round]));
    candleRef.current.setData(candleData);
    volumeRef.current.setData(displayCandles.map((candle) => ({
      time: candle.time,
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(242,54,69,.18)" : "rgba(8,153,129,.20)"
    })));
    ma5Ref.current.setData(ma5Data);
    ma10Ref.current.setData(ma10Data);
    if (displayCandles.length <= 12) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: -1,
        to: Math.max(24, displayCandles.length + 8)
      });
    } else {
      chartRef.current.timeScale().fitContent();
    }
  }, [candleData, displayCandles, ma5Data, ma10Data]);

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-ma5">MA5</span>
        <span className="legend-ma10">MA10</span>
        <span>{"\u6210\u4ea4\u91cf"}</span>
      </div>
      <div className="chart-host" ref={ref} />
    </div>
  );
}
