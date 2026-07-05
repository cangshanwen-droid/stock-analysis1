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
  label: string;
  time: Time;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
};

const START_DATE_UTC = Date.UTC(2026, 1, 26);
const CHART_BACKGROUND = "#080d16";
const GRID_MAJOR = "rgba(164, 180, 205, 0.09)";
const GRID_MINOR = "rgba(164, 180, 205, 0.025)";
const UP_COLOR = "#f23645";
const DOWN_COLOR = "#00b050";
const MA5_COLOR = "#f9c42f";
const MA10_COLOR = "#469fe6";

function round2(value: number) {
  return Number(value.toFixed(2));
}

function dateForIndex(index: number) {
  return new Date(START_DATE_UTC + index * 86400000).toISOString().slice(0, 10) as Time;
}

function timeKey(time: Time | unknown) {
  if (typeof time === "string" || typeof time === "number") return String(time);
  if (time && typeof time === "object" && "year" in time && "month" in time && "day" in time) {
    const item = time as { year: number; month: number; day: number };
    return `${item.year}-${String(item.month).padStart(2, "0")}-${String(item.day).padStart(2, "0")}`;
  }
  return "";
}

function expandCandles(candles: Candle[]): DisplayCandle[] {
  return candles.map((candle, index) => {
    const open = round2(candle.open);
    const close = round2(candle.close);
    return {
      round: candle.round,
      label: `R${candle.round}`,
      time: candle.time as Time,
      open,
      high: round2(candle.high),
      low: round2(Math.max(0.01, candle.low)),
      close,
      volume: Math.max(0, Math.round(candle.volume || 0)),
    };
  });
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
  const hasMeaningfulBars = candles.some((candle) => Math.round(candle.volume || 0) > 0 || Math.abs(candle.close - candle.open) > 0.005);

  if (!hasMeaningfulBars) {
    return (
      <div className="chart-shell chart-shell-empty">
        <div className="chart-empty-state">
          <strong>等待首笔成交</strong>
          <span>开盘后产生买卖成交，K 线将按比赛轮次更新</span>
        </div>
      </div>
    );
  }

  return <KlineChartCanvas candles={candles} />;
}

function KlineChartCanvas({ candles }: Props) {
  const ref = useRef<HTMLDivElement | null>(null);
  const tooltipRef = useRef<HTMLDivElement | null>(null);
  const chartRef = useRef<IChartApi | null>(null);
  const candleRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const ma5Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const ma10Ref = useRef<ISeriesApi<"Line"> | null>(null);
  const volumeMaRef = useRef<ISeriesApi<"Line"> | null>(null);
  const priceLinesRef = useRef<IPriceLine[]>([]);
  const roundLabelRef = useRef<Map<string, string>>(new Map());
  const candleLookupRef = useRef<Map<string, DisplayCandle>>(new Map());

  const displayCandles = useMemo(() => expandCandles(candles), [candles]);
  const latest = displayCandles[displayCandles.length - 1];
  const resistancePrice = useMemo(() => {
    if (!candles.length) return 0;
    return round2(Math.max(...candles.map((candle) => Math.max(candle.high, candle.close))));
  }, [candles]);

  const candleData = useMemo(() => displayCandles.map(({ time, open, high, low, close }) => ({
    time, open, high, low, close
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
        background: { type: ColorType.Solid, color: CHART_BACKGROUND },
        textColor: "#aeb9ca",
        fontFamily: "Inter, -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif",
        attributionLogo: false
      },
      localization: {
        priceFormatter: (price: number) => {
          if (Math.abs(price) >= 1000) return price.toFixed(0);
          if (Math.abs(price) >= 100) return price.toFixed(1);
          return price.toFixed(2);
        },
        timeFormatter: (time: Time) => {
          const label = roundLabelRef.current.get(timeKey(time));
          return label ?? "";
        }
      },
      grid: {
        vertLines: { color: GRID_MINOR, style: LineStyle.Solid, visible: false },
        horzLines: { color: GRID_MAJOR, style: LineStyle.Solid, visible: true }
      },
      crosshair: {
        mode: CrosshairMode.Normal,
        vertLine: { color: "rgba(137, 164, 198, 0.42)", width: 1, labelVisible: true, style: LineStyle.LargeDashed },
        horzLine: { color: "rgba(137, 164, 198, 0.42)", width: 1, labelVisible: true, style: LineStyle.LargeDashed }
      },
      rightPriceScale: {
        borderColor: "rgba(99, 116, 139, 0.42)",
        scaleMargins: { top: 0.08, bottom: 0.28 }
      },
      timeScale: {
        borderColor: "rgba(99, 116, 139, 0.42)",
        rightOffset: 8,
        barSpacing: 7,
        minBarSpacing: 4,
        fixLeftEdge: true,
        fixRightEdge: false,
        tickMarkFormatter: (time: Time) => {
          const label = roundLabelRef.current.get(timeKey(time));
          return label ?? "";
        }
      },
      handleScale: true,
      handleScroll: true
    });

    const candleSeries = chart.addCandlestickSeries({
      upColor: UP_COLOR,
      downColor: DOWN_COLOR,
      borderUpColor: UP_COLOR,
      borderDownColor: DOWN_COLOR,
      wickUpColor: UP_COLOR,
      wickDownColor: DOWN_COLOR,
      priceFormat: { type: "price", precision: 2, minMove: 0.01 },
      priceLineVisible: false,
      priceLineWidth: 1,
      lastValueVisible: false
    });

    const volumeSeries = chart.addHistogramSeries({
      priceFormat: { type: "volume" },
      priceScaleId: "",
      color: "rgba(148, 163, 184, 0.24)",
      lastValueVisible: false,
      priceLineVisible: false
    });
    volumeSeries.priceScale().applyOptions({
      scaleMargins: { top: 0.74, bottom: 0.02 }
    });

    const ma5Series = chart.addLineSeries({
      color: MA5_COLOR,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });

    const ma10Series = chart.addLineSeries({
      color: MA10_COLOR,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
    });

    const volumeMaSeries = chart.addLineSeries({
      color: "rgba(210, 218, 230, 0.55)",
      lineWidth: 1,
      priceScaleId: "",
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: false
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

    chart.subscribeCrosshairMove((param) => {
      const tooltip = tooltipRef.current;
      if (!tooltip || !ref.current || !param.time || !param.point || param.point.x < 0 || param.point.y < 0) {
        if (tooltip) tooltip.style.opacity = "0";
        return;
      }

      const candle = candleLookupRef.current.get(timeKey(param.time));
      if (!candle) {
        tooltip.style.opacity = "0";
        return;
      }

      const change = candle.close - candle.open;
      const changePct = candle.open ? (change / candle.open) * 100 : 0;
      tooltip.innerHTML = `
        <div class="kline-tip-head">第 ${candle.round} 轮</div>
        <div><span>开盘</span><strong>¥${candle.open.toFixed(2)}</strong></div>
        <div><span>最高</span><strong>¥${candle.high.toFixed(2)}</strong></div>
        <div><span>最低</span><strong>¥${candle.low.toFixed(2)}</strong></div>
        <div><span>收盘</span><strong>¥${candle.close.toFixed(2)}</strong></div>
        <div><span>涨跌</span><strong class="${change >= 0 ? "up" : "down"}">${change >= 0 ? "+" : ""}${change.toFixed(2)} (${changePct.toFixed(2)}%)</strong></div>
        <div><span>成交量</span><strong>${candle.volume}</strong></div>
      `;

      const x = param.point.x > ref.current.clientWidth - 190 ? param.point.x - 184 : param.point.x + 16;
      const y = Math.max(12, Math.min(param.point.y + 12, ref.current.clientHeight - 190));
      tooltip.style.transform = `translate(${x}px, ${y}px)`;
      tooltip.style.opacity = "1";
    });

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

    roundLabelRef.current = new Map(displayCandles.map((candle) => [timeKey(candle.time), candle.label]));
    candleLookupRef.current = new Map(displayCandles.map((candle) => [timeKey(candle.time), candle]));
    candleRef.current.setData(candleData);
    const volumeData = displayCandles.map((candle) => ({
      time: candle.time,
      value: candle.volume,
      color: candle.close >= candle.open ? "rgba(242,54,69,.38)" : "rgba(0,176,80,.36)"
    }));
    volumeRef.current.setData(volumeData);

    const ma5WithCrossColor = ma5Data.map((point, index) => ({
      ...point,
      color: ma10Data[index] && point.value < ma10Data[index].value ? "#a3aec0" : MA5_COLOR
    }));
    ma5Ref.current.setData(ma5WithCrossColor);
    ma10Ref.current.setData(ma10Data);
    volumeMaRef.current.setData(volumeMaData);

    priceLinesRef.current.forEach((line) => candleRef.current?.removePriceLine(line));
    priceLinesRef.current = [];

    const last = displayCandles[displayCandles.length - 1];
    if (last) {
      const currentLine = candleRef.current.createPriceLine({
        price: last.close,
        color: MA5_COLOR,
        lineWidth: 1,
        lineStyle: LineStyle.Solid,
        axisLabelVisible: false,
        title: ""
      });
      priceLinesRef.current.push(currentLine);

      if (resistancePrice > last.close) {
        const pressureLine = candleRef.current.createPriceLine({
          price: resistancePrice,
          color: "rgba(240, 245, 255, 0.88)",
          lineWidth: 2,
          lineStyle: LineStyle.LargeDashed,
          axisLabelVisible: true,
          title: ""
        });
        priceLinesRef.current.push(pressureLine);
      }
    }

    chartRef.current.timeScale().applyOptions({
      barSpacing: displayCandles.length <= 18 ? 7 : 6,
      rightOffset: displayCandles.length <= 18 ? 10 : 6
    });

    if (displayCandles.length <= 18) {
      chartRef.current.timeScale().setVisibleLogicalRange({
        from: -1,
        to: Math.max(28, displayCandles.length + 8)
      });
    } else {
      chartRef.current.timeScale().fitContent();
    }
  }, [candleData, displayCandles, ma5Data, ma10Data, resistancePrice, volumeMaData]);

  return (
    <div className="chart-shell">
      <div className="chart-legend">
        <span className="legend-ma5">MA5</span>
        <span className="legend-ma10">MA10</span>
        <span>VOL</span>
        {latest ? (
          <span className="legend-ohlc">
            O {latest.open.toFixed(2)} H {latest.high.toFixed(2)} L {latest.low.toFixed(2)} C {latest.close.toFixed(2)}
          </span>
        ) : null}
      </div>
      <div className="kline-tooltip" ref={tooltipRef} />
      <div className="chart-host" ref={ref} />
    </div>
  );
}
