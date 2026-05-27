import { BaseChart, type ChartAxis, type ChartData } from '@/views/chat/component/BaseChart.ts'
import { TableSheet, type S2Options, type S2DataConfig, type S2MountContainer } from '@antv/s2'
import { debounce } from 'lodash-es'

export class Table extends BaseChart {
  table?: TableSheet = undefined

  container: S2MountContainer | null = null

  debounceRender: any

  resizeObserver: ResizeObserver

  constructor(id: string) {
    super(id, 'table')
    this.container = document.getElementById(id)

    this.debounceRender = debounce(async (width?: number, height?: number) => {
      if (this.table) {
        this.table.changeSheetSize(width, height)
        await this.table.render(false)
      }
    }, 200)

    this.resizeObserver = new ResizeObserver(([entry] = []) => {
      const [size] = entry.borderBoxSize || []
      this.debounceRender(size.inlineSize, size.blockSize)
    })

    if (this.container?.parentElement) {
      this.resizeObserver.observe(this.container.parentElement)
    }
  }

  // 格式化数字为千位分隔符
  private formatNumber(value: any): string {
    if (value === null || value === undefined || value === '') {
      return '-'
    }
    // 尝试转换为数字
    const num = Number(value)
    if (isNaN(num)) {
      return String(value)
    }
    // 如果是整数，使用千位分隔符
    if (Number.isInteger(num)) {
      return num.toLocaleString('en-US')
    }
    // 如果是小数，保留两位小数并使用千位分隔符
    return num.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  }

  // 判断字段是否为数值类型
  private isNumericField(value: any): boolean {
    if (value === null || value === undefined || value === '') {
      return false
    }
    const num = Number(value)
    return !isNaN(num) && isFinite(num)
  }

  // 计算汇总行数据
  private calculateSummaryRow(axis: Array<ChartAxis>, data: Array<ChartData>): ChartData {
    const summaryRow: ChartData = {
      __index__: '汇总',
    }
    
    axis.forEach((col) => {
      const values = data.map(row => row[col.value]).filter(v => this.isNumericField(v))
      
      if (values.length > 0) {
        // 计算数值字段的总和
        const sum = values.reduce((acc, val) => acc + Number(val), 0)
        summaryRow[col.value] = this.formatNumber(sum)
      } else {
        // 非数值字段显示空或"汇总"
        summaryRow[col.value] = ''
      }
    })
    
    return summaryRow
  }

  // 处理数据：添加序号列、格式化数值和汇总行
  private processData(axis: Array<ChartAxis>, data: Array<ChartData>): { processedAxis: Array<ChartAxis>, processedData: Array<ChartData> } {
    if (!data || data.length === 0) {
      return { processedAxis: axis, processedData: [] }
    }

    // 添加序号列
    const indexAxis: ChartAxis = {
      name: '序号',
      value: '__index__',
    }
    const processedAxis = [indexAxis, ...axis]
    
    // 处理数据：添加序号和格式化数值
    const processedData = data.map((row, index) => {
      const processedRow: ChartData = {
        __index__: index + 1,
      }
      
      // 复制原始数据并格式化数值
      axis.forEach((col) => {
        const value = row[col.value]
        // 判断是否为数值类型
        if (this.isNumericField(value)) {
          processedRow[col.value] = this.formatNumber(value)
        } else {
          processedRow[col.value] = value
        }
      })
      
      return processedRow
    })
    
    // 添加汇总行
    const summaryRow = this.calculateSummaryRow(axis, data)
    processedData.push(summaryRow)
    
    return { processedAxis, processedData }
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>) {
    // 处理数据：添加序号和格式化
    const { processedAxis, processedData } = this.processData(axis, data)
    
    super.init(processedAxis, processedData)

    const s2DataConfig: S2DataConfig = {
      fields: {
        columns: this.axis?.map((a) => a.value) ?? [],
      },
      meta:
        this.axis?.map((a) => {
          return {
            field: a.value,
            name: a.name,
          }
        }) ?? [],
      data: this.data,
    }

    const s2Options: S2Options = {
      width: 600,
      height: 360,
      placeholder: {
        cell: '-',
        empty: {
          icon: 'Empty',
          description: 'No Data',
        },
      },
    }

    if (this.container) {
      this.table = new TableSheet(this.container, s2DataConfig, s2Options)
    }
  }

  render() {
    this.table?.render()
  }

  destroy() {
    this.table?.destroy()
    this.resizeObserver?.disconnect()
  }
}
