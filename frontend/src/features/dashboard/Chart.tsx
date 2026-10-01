import * as echarts from 'echarts/core'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { GridComponent, TooltipComponent } from 'echarts/components'
import { CanvasRenderer } from 'echarts/renderers'
import { useEffect, useRef } from 'react'

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, CanvasRenderer])

interface TileData {
  type: string
  categories: string[]
  series: { name: string; values: (number | null)[]; formatted: string[] }[]
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
  useEffect(() => {
    onSelectRef.current = onSelect
  }, [onSelect])
  useEffect(() => {
    if (!ref.current) return
    const chart = echarts.init(ref.current)
    const values = tile.series[0]?.values ?? []
    const cats = tile.categories
    let option: Record<string, unknown>
    if (tile.type === 'donut' || tile.type === 'funnel' || tile.type === 'gauge') {
      option = {
        series: [
          {
            type: 'pie',
            radius: tile.type === 'donut' ? ['40%', '70%'] : ['0%', '70%'],
            data: cats.map((name, i) => ({ name, value: values[i] ?? 0 })),
          },
        ],
      }
    } else if (tile.type === 'line' || tile.type === 'area') {
      option = {
        xAxis: { type: 'category', data: cats },
        yAxis: { type: 'value' },
        series: tile.series.map((s) => ({
          type: 'line',
          name: s.name,
          data: s.values,
          areaStyle: tile.type === 'area' ? {} : undefined,
        })),
      }
    } else if (tile.type === 'bar_h' || tile.type === 'bullet') {
      option = {
        yAxis: { type: 'category', data: cats },
        xAxis: { type: 'value' },
        series: tile.series.map((s) => ({ type: 'bar', name: s.name, data: s.values })),
      }
    } else {
      option = {
        xAxis: { type: 'category', data: cats },
        yAxis: { type: 'value' },
        series: tile.series.map((s) => ({
          type: 'bar',
          name: s.name,
          data: s.values,
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
  }, [tile])
  return <div ref={ref} role="img" aria-label={tile.type} style={{ height: 240 }} />
}
