import * as echarts from 'echarts/core'
import { BarChart, GaugeChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef, useState } from 'react'

echarts.use([BarChart, GaugeChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

interface TileData {
  type: string
  categories: string[]
  series: { name: string; values: (number | null)[]; formatted: string[] }[]
}

function themeColors() {
  const css = getComputedStyle(document.documentElement)
  const read = (name: string) => css.getPropertyValue(name).trim()
  return {
    text: read('--text'),
    muted: read('--muted'),
    border: read('--border'),
    blue: read('--blue'),
    teal: read('--teal'),
    green: read('--green'),
    amber: read('--amber'),
    purple: read('--purple'),
  }
}

/** Lazy-loaded ECharts wrapper (tree-shaken chart types only). */
export default function Chart({
  tile,
  onSelect,
}: {
  tile: TileData
  onSelect?: (category: string) => void
}) {
  const ref = useRef<HTMLDivElement>(null)
  const onSelectRef = useRef(onSelect)
  const [themeKey, setThemeKey] = useState(
    () => document.documentElement.getAttribute('data-theme') ?? 'dark',
  )
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    const root = document.documentElement
    const sync = () => setThemeKey(root.getAttribute('data-theme') ?? 'dark')
    const observer = new MutationObserver(sync)
    observer.observe(root, { attributes: true, attributeFilter: ['data-theme'] })
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    const palette = themeColors()
    const values = tile.series[0]?.values ?? []
    const cats = tile.categories
    const seriesColors = [palette.blue, palette.teal, palette.green, palette.amber, palette.purple]
    const axis = {
      axisLine: { lineStyle: { color: palette.border } },
      axisLabel: { color: palette.muted },
      splitLine: { lineStyle: { color: palette.border, opacity: 0.35 } },
    }
    let option: Record<string, unknown>
    if (tile.type === 'gauge') {
      option = {
        series: [
          {
            type: 'gauge',
            min: 0,
            max: 100,
            startAngle: 210,
            endAngle: -30,
            progress: { show: true, width: 12 },
            pointer: { show: false },
            axisLine: { lineStyle: { width: 12, color: [[1, palette.border]] } },
            axisTick: { show: false },
            splitLine: { show: false },
            axisLabel: { show: false },
            anchor: { show: false },
            detail: { valueAnimation: false, fontSize: 22, color: palette.text, formatter: '{value}' },
            data: [{ value: Number(values[0] ?? 0), name: cats[0] ?? '' }],
            itemStyle: { color: palette.teal },
          },
        ],
      }
    } else if (tile.type === 'donut' || tile.type === 'funnel') {
      option = {
        color: seriesColors,
        tooltip: { trigger: 'item' },
        series: [
          {
            type: 'pie',
            radius: tile.type === 'donut' ? ['40%', '70%'] : ['0%', '70%'],
            data: cats.map((name, i) => ({ name, value: values[i] ?? 0 })),
            itemStyle: { borderColor: palette.border, borderWidth: 1 },
            label: { color: palette.text },
          },
        ],
      }
    } else if (tile.type === 'line' || tile.type === 'area') {
      option = {
        color: seriesColors,
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: cats, ...axis },
        yAxis: { type: 'value', ...axis },
        series: tile.series.map((s) => ({
          type: 'line',
          name: s.name,
          data: s.values,
          smooth: true,
          areaStyle: tile.type === 'area' ? { opacity: 0.18 } : undefined,
        })),
      }
    } else if (tile.type === 'bar_h' || tile.type === 'bullet') {
      option = {
        color: seriesColors,
        tooltip: { trigger: 'axis' },
        yAxis: { type: 'category', data: cats, ...axis },
        xAxis: { type: 'value', ...axis },
        series: tile.series.map((s) => ({ type: 'bar', name: s.name, data: s.values, barMaxWidth: 28 })),
      }
    } else {
      option = {
        color: seriesColors,
        tooltip: { trigger: 'axis' },
        xAxis: { type: 'category', data: cats, ...axis },
        yAxis: { type: 'value', ...axis },
        series: tile.series.map((s) => ({
          type: 'bar',
          name: s.name,
          data: s.values,
          barMaxWidth: 28,
          stack: tile.type === 'stacked_bar' ? 'total' : undefined,
        })),
      }
    }
    chart.setOption(option)
    chart.on('click', (params: { name?: string }) => {
      if (params.name) onSelectRef.current?.(params.name)
    })
    const onResize = () => chart.resize()
    window.addEventListener('resize', onResize)
    return () => {
      window.removeEventListener('resize', onResize)
      chart.dispose()
    }
  }, [tile, themeKey])
  return <div ref={ref} role="img" aria-label={tile.type} style={{ height: 240 }} />
}
