import { useEffect } from 'react'
import type { RefObject } from 'react'
import Plotly from 'plotly.js-dist-min'
import {
  downsamplePoints,
  getColumnLabel,
  getNumericPoints,
  getSeriesColor,
} from '../lib/data'
import type {
  ChartSettings,
  DataSet,
  DerivedSeries,
} from '../types'

interface PlotCanvasProps {
  plotRef: RefObject<HTMLDivElement | null>
  datasets: DataSet[]
  derivedSeries: DerivedSeries[]
  settings: ChartSettings
}

export function PlotCanvas({
  plotRef,
  datasets,
  derivedSeries,
  settings,
}: PlotCanvasProps) {
  useEffect(() => {
    const root = plotRef.current
    if (!root) return

    const traces: Record<string, unknown>[] = []
    for (const dataset of datasets) {
      if (!dataset.visible) continue
      dataset.yColumns.forEach((columnKey, index) => {
        const points = downsamplePoints(
          getNumericPoints(dataset, dataset.xColumn, columnKey).sort(
            (a, b) => a.x - b.x,
          ),
        )
        if (points.length === 0) return
        const color = getSeriesColor(dataset, columnKey)
        traces.push({
          type: 'scatter',
          mode: settings.showMarkers ? 'lines+markers' : 'lines',
          name: `${dataset.name}: ${getColumnLabel(dataset, columnKey)}`,
          x: points.map((point) => point.x),
          y: points.map((point) => point.y),
          line: {
            color,
            width: settings.lineWidth,
            shape: settings.smoothLines ? 'spline' : 'linear',
          },
          marker: { color, size: settings.markerSize },
          hovertemplate: `%{y:.6g}<extra>${dataset.name} / ${getColumnLabel(
            dataset,
            columnKey,
          )}</extra>`,
          customdata: points.map(() => index),
        })
      })
    }

    const visibleDerivedSeries: {
      series: DerivedSeries
      points: { x: number; y: number }[]
    }[] = []
    let hasVisibleFft = false
    for (const series of derivedSeries) {
      if (!series.visible) continue
      const finitePoints = series.points.filter(
        (point) => Number.isFinite(point.x) && Number.isFinite(point.y),
      )
      const points = downsamplePoints(finitePoints)
      if (points.length === 0) continue
      visibleDerivedSeries.push({ series, points })
      if (series.kind === 'fft') hasVisibleFft = true
    }

    for (const [index, { series, points }] of visibleDerivedSeries.entries()) {
      traces.push({
        type: 'scatter',
        mode: 'lines',
        name: series.label,
        x: points.map((point) => point.x),
        y: points.map((point) => point.y),
        xaxis: series.kind === 'fft' ? 'x2' : 'x',
        yaxis: `y${index + 2}`,
        line: {
          color: series.color,
          width: Math.max(2, settings.lineWidth),
          dash: series.kind === 'regression' ? 'dash' : 'solid',
        },
        hovertemplate: `x: %{x:.6g}<br>y: %{y:.6g}<extra>${series.label}</extra>`,
      })
    }

    const hasVisibleDerived = visibleDerivedSeries.length > 0
    const hasMultipleVisibleDerived = visibleDerivedSeries.length > 1
    const derivedAxisLayout = Object.fromEntries(
      visibleDerivedSeries.map((_, index) => {
        const showScale = !hasMultipleVisibleDerived
        return [
          `yaxis${index + 2}`,
          {
            ...(index === 0
              ? {
                  title: {
                    text: hasMultipleVisibleDerived
                      ? '分析值（独立刻度）'
                      : '分析值',
                    standoff: 12,
                  },
                }
              : {}),
            type: 'linear',
            overlaying: 'y',
            side: 'right',
            showgrid: false,
            showline: showScale,
            showticklabels: showScale,
            zeroline: showScale,
            zerolinecolor: '#cfd7d1',
            linecolor: '#9ca8a1',
            tickcolor: '#9ca8a1',
            automargin: showScale,
          },
        ]
      }),
    )

    const layout = {
      title: {
        text: settings.title,
        x: 0.04,
        xanchor: 'left',
        font: { size: 17, color: '#172321', weight: 600 },
      },
      paper_bgcolor: 'rgba(0,0,0,0)',
      plot_bgcolor: '#fbfcfa',
      font: {
        family:
          '"IBM Plex Sans", "Noto Sans SC", "Microsoft YaHei", sans-serif',
        color: '#35413e',
        size: 12,
      },
      margin: {
        l: 72,
        r: hasVisibleDerived ? 64 : 30,
        t: settings.title ? (hasVisibleFft ? 142 : 112) : hasVisibleFft ? 70 : 34,
        b: 62,
      },
      hovermode: 'x unified',
      hoverlabel: {
        bgcolor: '#172321',
        bordercolor: '#172321',
        font: { color: '#f5f7f2', size: 12 },
      },
      showlegend: settings.showLegend,
      legend: {
        orientation: 'h',
        x: 0.04,
        y: settings.title ? 1 : 1.05,
        xanchor: 'left',
        yanchor: 'bottom',
        font: { size: 11 },
        bgcolor: 'rgba(255,255,255,0)',
      },
      xaxis: {
        title: { text: settings.xLabel, standoff: 16 },
        type: settings.xScale,
        showgrid: settings.showGrid,
        gridcolor: '#dfe4df',
        zerolinecolor: '#b8c1bb',
        linecolor: '#9ca8a1',
        tickcolor: '#9ca8a1',
        automargin: true,
      },
      yaxis: {
        title: { text: settings.yLabel, standoff: 14 },
        type: settings.yScale,
        showgrid: settings.showGrid,
        gridcolor: '#dfe4df',
        zerolinecolor: '#b8c1bb',
        linecolor: '#9ca8a1',
        tickcolor: '#9ca8a1',
        automargin: true,
      },
      ...derivedAxisLayout,
      ...(hasVisibleFft
        ? {
            xaxis2: {
              title: {
                text: '频率（1 / 横轴单位）',
                standoff: 8,
              },
              type: 'linear',
              overlaying: 'x',
              side: 'top',
              showgrid: false,
              zeroline: false,
              linecolor: '#9ca8a1',
              tickcolor: '#9ca8a1',
              automargin: true,
            },
          }
        : {}),
      uirevision: 'plot-workbench',
      dragmode: 'pan',
    }

    const config = {
      responsive: true,
      displayModeBar: false,
      displaylogo: false,
      scrollZoom: true,
      toImageButtonOptions: {
        format: 'png',
        filename: 'plot-chart',
        scale: 2,
      },
    }

    void Plotly.react(root, traces, layout, config)

    const resizeObserver = new ResizeObserver(() => {
      Plotly.Plots.resize(root)
    })
    resizeObserver.observe(root)

    return () => {
      resizeObserver.disconnect()
    }
  }, [datasets, derivedSeries, plotRef, settings])

  useEffect(
    () => () => {
      if (plotRef.current) Plotly.purge(plotRef.current)
    },
    [plotRef],
  )

  return (
    <div
      ref={plotRef}
      className="plot-canvas"
      role="img"
      aria-label="数据曲线图"
    />
  )
}
