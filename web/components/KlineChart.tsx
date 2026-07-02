"use client";

import { useEffect, useMemo, useRef } from "react";
import {
  ColorType,
  CrosshairMode,
  createChart,
  LineStyle,
  type IChartApi,
  type IPriceLine,
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
const START_DATE_UTC = Date.UTC(2026, 1, 26);

function round2(value: number) {
  return Number(value.toFixed(2));
}

function dateForIndex(index: number) {
  return new Date(START_DATE_UTC + index * 86400000).toISOString().slice(0, 10) as Time;
}

function expandCandles(candles: Candle[]): DisplayCandle[] {
  return candles.map((candle, index) => ({
    round: candle.round,
    time: dateForIndex(index),
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
  const volumeMaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);

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
  const volumeMaData = useMemo(() => displayCandles.map((candle, index) => ({
    time: candle.time,
    value: round2(
      displayCandles
        .slice(Math.max(0, index - 4), index + 1)
        .reduce((sum, item) => sum + item.volume, 0) / Math.min(5, index + 1)
    )
  })), [displayCandles]);

  useEffect(() => {
    if (!ref.current) return;
    const chart = createChart(ref.current, {
      width: ref.current.clientWidth,
      height: ref.current.clientHeight,
      layout: {
        background: { type: ColorType.Solid, color: "#090d16" },
        textColor: "#a3aec0",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        attributionLogo: false
      },
      grid: {
        vertLines: { visible: false },
        horzLines: { color: "rgba(168, 179, 196, 0.12)" }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "#7890ad", width: 1, labelBackgroundColor: "#1f2b3d" },
        horzLine: { color: "#7890ad", width: 1, labelBackgroundColor: "#1f2b3d" }
      },
      rightPriceScale: {
        borderColor: "#263448",
        scaleMargins: { top: 0.08, bottom: 0.3 }
      },
      timeScale: {
        borderColor: "#263448",
        rightOffset: 10,
        barSpacing: 24,
        minBarSpacing: 10,
        fixLeftEdge: true,
        fixRightEdge: false,
        tickMarkFormatter: (time: Time) => {
          const value = String(time);
          const date = value.length >= 10 ? new Date(`${value}T00:00:00Z`) : new Date(Number(value) * 1000);
          if (Number.isNaN(date.getTime())) return "";
          return `${date.getMonth() + 1}\u6708${date.getDate()}\u65e5`;
        }
      },
      handleScale: true,
      handleScroll: true
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: "#26c296",
      downColor: "#b52a40",
      borderUpColor: "#26c296",
      borderDownColor: "#b52a40",
      wickUpColor: "#26c296",
      wickDownColor: "#b52a40",
      priceLineVisible: false,
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
      color: "#f9c42f",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const ma10Series = chart.addLineSeries({
      color: "#469fe6",
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false
    });

    const volumeMaSeries = chart.addLineSeries({
      color: "rgba(210, 218, 230, 0.55)",
      lineWidth: 1,
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false
    });

    chartRef.current = chart;
    candleRef.current = candleSeries;
    volumeRef.current = volumeSeries;
    ma5Ref.current = ma5Series;
    ma10Ref.current = ma10Series;
    volumeMaRef.current = volumeMaSeries;

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
      volumeMaRef.current = null;
      priceLinesRef.current = [];
    };
  }, []);

  useEffect(() => {
    if (!candleRef.current || !volumeRef.current || !ma5Ref.current || !ma10Ref.current || !volumeMaRef.current || !chartRef.current) return;
    candleRef.current.setData(candleData);
    volumeRef.current.setData(displayCandles.map((candle) => ({
      time: candle.time,
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(38,194,150,.32)" : "rgba(181,42,64,.34)"
    })));
    const ma5WithCrossColor = ma5Data.map((point, index) => ({
      ...point,
      color: ma10Data[index] && point.value < ma10Data[index].value ? "#a3aec0" : "#f9c42f"
    }));
    ma5Ref.current.setData(ma5WithCrossColor);
    ma10Ref.current.setData(ma10Data);
    volumeMaRef.current.setData(volumeMaData);

    priceLinesRef.current.forEach((line) => candleRef.current?.removePriceLine(line));
    priceLinesRef.current = [];
    const last = displayCandles[displayCandles.length - 1];
    if (last) {
      const pressure = Math.max(...displayCandles.slice(0, -1).map((candle) => candle.high), last.high);
      const currentLine = candleRef.current.createPriceLine({
        price: last.close,
        color: "#f9c42f",
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: true,
        title: ""
      });
      priceLinesRef.current.push(currentLine);
      if (pressure > last.close) {
        const pressureLine = candleRef.current.createPriceLine({
          price: round2(pressure),
          color: "rgba(240, 245, 255, 0.88)",
          lineWidth: 2,
          lineStyle: LineStyle.LargeDashed,
          axisLabelVisible: true,
          title: ""
        });
        priceLinesRef.current.push(pressureLine);
      }
    }
    if (displayCandles.length <= 12) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: -1,
        to: Math.max(24, displayCandles.length + 8)
      });
    } else {
      chartRef.current.timeScale().fitContent();
    }
  }, [candleData, displayCandles, ma5Data, ma10Data, volumeMaData]);

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
