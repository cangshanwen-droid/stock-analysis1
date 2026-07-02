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

const PREVIEW_BARS = 34;
const ROUND_SEGMENTS = 10;
const DISPLAY_START_TIME = 946684800;
const DISPLAY_STEP_SECONDS = 21600;

function round2(value: number) {
  return Number(value.toFixed(2));
}

function seedFromCandles(candles: Candle[]) {
  return candles.reduce((seed, candle) => (
    seed + candle.round * 97 + Math.round(candle.open * 31) + Math.round(candle.close * 43) + Math.round(candle.volume || 0)
  ), 137);
}

function createRandom(seed: number) {
  let state = seed >>> 0;
  return () => {
    state = (state * 1664525 + 1013904223) >>> 0;
    return state / 4294967296;
  };
}

function normalish(random: () => number) {
  return (random() + random() + random() + random() - 2) / 2;
}

function appendSyntheticBar(
  output: DisplayCandle[],
  source: Candle,
  open: number,
  close: number,
  range: number,
  volumeBase: number,
  random: () => number,
  isAnchor = false
) {
  const index = output.length;
  const upperWick = Math.max(range * (0.12 + random() * 0.38), Math.max(open, close) * 0.0018);
  const lowerWick = Math.max(range * (0.12 + random() * 0.36), Math.max(open, close) * 0.0018);
  const turnoverPulse = 0.72 + random() * 0.62 + Math.abs(close - open) / Math.max(range, 0.01) * 0.22;

  output.push({
    round: source.round,
    time: (DISPLAY_START_TIME + index * DISPLAY_STEP_SECONDS) as Time,
    open: round2(open),
    high: round2(Math.max(open, close) + upperWick),
    low: round2(Math.max(0.01, Math.min(open, close) - lowerWick)),
    close: round2(Math.max(0.01, close)),
    volume: Math.max(1, Math.round(volumeBase * turnoverPulse)),
    isAnchor
  });
}

function expandCandles(candles: Candle[]): DisplayCandle[] {
  const expanded: DisplayCandle[] = [];
  if (!candles.length) return expanded;

  const random = createRandom(seedFromCandles(candles));
  const first = candles[0];
  const firstPrice = Math.max(0.01, first.open || first.close || 1);
  const previewRange = Math.max(firstPrice * 0.012, Math.abs(first.high - first.low) * 0.45, 0.08);
  let open = round2(firstPrice * (1 + normalish(random) * 0.012));

  for (let index = 0; index < PREVIEW_BARS; index += 1) {
    const remaining = PREVIEW_BARS - index;
    const pullToAnchor = (first.open - open) / remaining;
    const shock = normalish(random) * previewRange * 0.36;
    const close = index === PREVIEW_BARS - 1
      ? first.open
      : Math.max(0.01, open + pullToAnchor + shock);
    appendSyntheticBar(expanded, first, open, close, previewRange, (first.volume || 1200) / 7, random);
    open = close;
  }

  candles.forEach((candle, candleIndex) => {
    const sourceOpen = candleIndex === 0 ? first.open : candles[candleIndex - 1].close;
    const targetClose = candle.close;
    const baseRange = Math.max(
      Math.abs(targetClose - sourceOpen),
      Math.abs(candle.high - candle.low),
      Math.max(targetClose, sourceOpen) * 0.02,
      0.12
    );
    let segmentOpen = expanded.length ? expanded[expanded.length - 1].close : sourceOpen;

    for (let step = 1; step <= ROUND_SEGMENTS; step += 1) {
      const remaining = ROUND_SEGMENTS - step + 1;
      const pullToAnchor = (targetClose - segmentOpen) / remaining;
      const shockScale = baseRange * (0.42 * Math.sin(Math.PI * step / ROUND_SEGMENTS) + 0.08);
      const close = step === ROUND_SEGMENTS
        ? targetClose
        : Math.max(0.01, segmentOpen + pullToAnchor + normalish(random) * shockScale);
      appendSyntheticBar(
        expanded,
        candle,
        segmentOpen,
        close,
        baseRange,
        (candle.volume || 1200) / ROUND_SEGMENTS,
        random,
        step === ROUND_SEGMENTS
      );
      segmentOpen = close;
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
        rightOffset: 4,
        barSpacing: 13,
        minBarSpacing: 5,
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
      upColor: "rgba(0,0,0,0)",
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
      color: candle.close >= candle.open ? "rgba(242,54,69,.28)" : "rgba(8,153,129,.30)"
    })));
    ma5Ref.current.setData(ma5Data);
    ma10Ref.current.setData(ma10Data);
    chartRef.current.timeScale().fitContent();
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
