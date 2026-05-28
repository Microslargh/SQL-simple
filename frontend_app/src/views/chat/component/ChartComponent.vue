<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted } from 'vue'
import { getChartInstance } from '@/views/chat/component/index.ts'
import type { BaseChart, ChartAxis, ChartData } from '@/views/chat/component/BaseChart.ts'
import { useEmitt } from '@/utils/useEmitt.ts'

const params = withDefaults(
  defineProps<{
    id: string | number
    type: string
    data?: Array<ChartData>
    columns?: Array<ChartAxis>
    x?: Array<ChartAxis>
    y?: Array<ChartAxis>
    series?: Array<ChartAxis>
    question?: string
  }>(),
  {
    data: () => [],
    columns: () => [],
    x: () => [],
    y: () => [],
    series: () => [],
    question: '',
  }
)

const chartId = computed(() => {
  return 'chart-component-' + params.id
})

const axis = computed(() => {
  const _list: Array<ChartAxis> = []
  params.columns.forEach((column) => {
    _list.push({ name: column.name, value: column.value })
  })
  params.x.forEach((column) => {
    _list.push({ name: column.name, value: column.value, type: 'x' })
  })
  params.y.forEach((column) => {
    _list.push({ name: column.name, value: column.value, type: 'y' })
  })
  params.series.forEach((column) => {
    _list.push({ name: column.name, value: column.value, type: 'series' })
  })
  return _list
})

let chartInstance: BaseChart | undefined

const tableOptions = computed(() => {
  const q = params.question || ''
  const hasCompositionKeywords = ['构成', '组成', '占比'].some((kw) => q.includes(kw))
  const isSingleRow = params.data.length === 1 && axis.value.length >= 4
  return {
    showSummaryRow: !hasCompositionKeywords,
    transpose: isSingleRow,
  }
})

function renderChart() {
  chartInstance = getChartInstance(params.type, chartId.value)
  if (chartInstance) {
    chartInstance.init(axis.value, params.data, tableOptions.value)
    chartInstance.render()
  }
  console.debug(chartInstance)
}

function destroyChart() {
  if (chartInstance) {
    chartInstance.destroy()
    chartInstance = undefined
  }
}

function getExcelData() {
  return {
    axis: axis.value,
    data: params.data,
  }
}

useEmitt({
  name: 'view-render-all',
  callback: renderChart,
})

useEmitt({
  name: `view-render-${params.id}`,
  callback: renderChart,
})

defineExpose({
  renderChart,
  destroyChart,
  getExcelData,
})

onMounted(() => {
  nextTick(() => {
    renderChart()
  })
})

onUnmounted(() => {
  destroyChart()
})
</script>

<template>
  <div :id="chartId" class="chart-container" :class="{ 'chart-container--table': params.type === 'table' }"></div>
</template>

<style scoped lang="less">
.chart-container {
  height: 100%;
  width: 100%;

  &.chart-container--table {
    height: auto;
  }
}
</style>
