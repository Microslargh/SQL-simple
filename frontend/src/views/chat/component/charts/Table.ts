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

  // 处理数据：添加序号列和格式化数值
  private processData(axis: Array<ChartAxis>, data: Array<ChartData>): { processedAxis: Array<ChartAxis>, processedData: Array<ChartData> } {
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
        if (value !== null && value !== undefined && value !== '') {
          const num = Number(value)
          if (!isNaN(num)) {
            processedRow[col.value] = this.formatNumber(value)
          } else {
            processedRow[col.value] = value
          }
        } else {
          processedRow[col.value] = value
        }
      })
      
      return processedRow
    })
    
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
