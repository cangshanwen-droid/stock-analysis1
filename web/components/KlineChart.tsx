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

const DISPLAY_SEGMENTS = 6;
const DISPLAY_START_TIME = 946684800;
const DISPLAY_STEP_SECONDS = 21600;

function round2(value: number) {
  return Number(value.toFixed(2));
}

function expandCandles(candles: Candle[]): DisplayCandle[] {
  const expanded: DisplayCandle[] = [];
  if (!candles.length) return expanded;

  candles.forEach((candle, candleIndex) => {
    const sourceOpen = candleIndex === 0 ? candle.open : candles[candleIndex - 1].close;
    const targetClose = candle.close;
    const direction = targetClose >= sourceOpen ? 1 : -1;
    const baseRange = Math.max(
      Math.abs(targetClose - sourceOpen),
      Math.abs(candle.high - candle.low),
      Math.max(targetClose, sourceOpen) * 0.024,
      0.18
    );
    let segmentOpen = candleIndex === 0 ? candle.open : expanded[expanded.length - 1].close;

    for (let step = 1; step <= DISPLAY_SEGMENTS; step += 1) {
      const progress = step / DISPLAY_SEGMENTS;
      const index = expanded.length;
      const wave = step === DISPLAY_SEGMENTS
        ? 0
        : Math.sin((candle.round + step) * 1.37) * baseRange * 0.22;
      const drift = (targetClose - sourceOpen) * progress;
      const segmentClose = step === DISPLAY_SEGMENTS
        ? targetClose
        : Math.max(0.01, sourceOpen + drift + wave);
      const wick = baseRange * (0.2 + ((candle.round + step) % 4) * 0.07);
      const high = Math.max(segmentOpen, segmentClose) + wick;
      const low = Math.max(0.01, Math.min(segmentOpen, segmentClose) - wick * 0.82);

      expanded.push({
        round: candle.round,
        time: (DISPLAY_START_TIME + index * DISPLAY_STEP_SECONDS) as Time,
        open: round2(segmentOpen),
        high: round2(high),
        low: round2(low),
        close: round2(segmentClose),
        volume: Math.max(1, Math.round((candle.volume || 1000) / DISPLAY_SEGMENTS * (0.82 + progress * 0.36))),
        isAnchor: step === DISPLAY_SEGMENTS
      });

      segmentOpen = segmentClose + (step < DISPLAY_SEGMENTS ? direction * baseRange * 0.03 : 0);
    }
  });

  return expanded;
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
        barSpacing: 12,
        tickMarkFormatter: (time: Time) => {
          const round = roundLabelRef.current.get(String(time));
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
    roundLabelRef.current = new Map(displayCandles.filter((candle) => candle.isAnchor).map((candle) => [String(candle.time), candle.round]));
    candleRef.current.setData(candleData);
    volumeRef.current.setData(displayCandles.map((candle) => ({
      time: candle.time,
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(242,54,69,.36)" : "rgba(8,153,129,.36)"
    })));
    ma5Ref.current.setData(ma5Data);
    ma10Ref.current.setData(ma10Data);

    if (displayCandles.length <= 24) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: -2,
        to: Math.max(24, displayCandles.length + 4)
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
        <span>成交量</span>
      </div>
      <div className="chart-host" ref={ref} />
    </div>
  );
}
