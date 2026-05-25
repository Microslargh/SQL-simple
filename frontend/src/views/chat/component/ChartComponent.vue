<script setup lang="ts">
import { computed, nextTick, onMounted, onUnmounted } from 'vue'
import { getChartInstance } from '@/views/chat/component/index.ts'
import type { BaseChart, ChartAxis, ChartData } from '@/views/chat/component/BaseChart.ts'
import { isSingleRowMultiColumn } from '@/views/chat/component/charts/Table.ts'
import { useEmitt } from '@/utils/useEmitt.ts'

/** 是否为不展示汇总行的问题：构成类（如两金构成）、一利五率等已是总-分或指标结构，表格不展示汇总行避免重复相加 */
function isCompositionQuestion(question: string | undefined): boolean {
  if (!question || typeof question !== 'string') return false
  const q = question.trim()
  if (q.includes('两金构成')) return true
  if (q.includes('两金') && q.includes('构成')) return true
  if (q.includes('一利五率')) return true
  return false
}

const params = withDefaults(
  defineProps<{
    id: string | number
    type: string
    data?: Array<ChartData>
    columns?: Array<ChartAxis>
    x?: Array<ChartAxis>
    y?: Array<ChartAxis>
    series?: Array<ChartAxis>
    /** 用户问题，用于表格是否展示汇总行（如两金构成时不展示） */
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

function renderChart() {
  chartInstance = getChartInstance(params.type, chartId.value)
  if (chartInstance) {
    const tableOptions =
      params.type === 'table'
        ? { 
            showSummaryRow: !isCompositionQuestion(params.question) && !isSingleRowMultiColumn(params.data, axis.value) && params.data.length > 1,
            transpose: isSingleRowMultiColumn(params.data, axis.value)
          }
        : undefined
    chartInstance.init(axis.value, params.data, tableOptions)
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
