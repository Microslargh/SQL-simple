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

  // 判断是否为日期/年份格式（YYYY、YYYYMM、YYYYMMDD等）
  private isDateOrYearFormat(value: any): boolean {
    if (value === null || value === undefined || value === '') {
      return false
    }
    const str = String(value).trim()
    // 检查是否为4位、6位或8位纯数字（可能是年份格式）
    if (/^\d{4}$/.test(str)) {
      // 4位数字，可能是年份（如2024）
      const year = parseInt(str, 10)
      // 合理的年份范围：1900-2100
      if (year >= 1900 && year <= 2100) {
        return true
      }
    } else if (/^\d{6}$/.test(str)) {
      // 6位数字，可能是YYYYMM格式（如202405）
      const year = parseInt(str.substring(0, 4), 10)
      const month = parseInt(str.substring(4, 6), 10)
      if (year >= 1900 && year <= 2100 && month >= 1 && month <= 12) {
        return true
      }
    } else if (/^\d{8}$/.test(str)) {
      // 8位数字，可能是YYYYMMDD格式（如20240501）
      const year = parseInt(str.substring(0, 4), 10)
      const month = parseInt(str.substring(4, 6), 10)
      const day = parseInt(str.substring(6, 8), 10)
      if (year >= 1900 && year <= 2100 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        return true
      }
    }
    return false
  }

  // 格式化数字为千位分隔符
  private formatNumber(value: any): string {
    if (value === null || value === undefined || value === '') {
      return '-'
    }
    
    // 如果是日期/年份格式，不进行数字格式化
    if (this.isDateOrYearFormat(value)) {
      return String(value)
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
    
    // 如果是日期/年份格式，不视为数值类型
    if (this.isDateOrYearFormat(value)) {
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

  // 处理数据：添加序号列、格式化数值，可选添加汇总行（构成类问题不汇总）
  private processData(
    axis: Array<ChartAxis>,
    data: Array<ChartData>,
    addSummaryRow: boolean = true
  ): { processedAxis: Array<ChartAxis>; processedData: Array<ChartData> } {
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

      axis.forEach((col) => {
        const value = row[col.value]
        if (this.isNumericField(value)) {
          processedRow[col.value] = this.formatNumber(value)
        } else {
          processedRow[col.value] = value
        }
      })

      return processedRow
    })

    if (addSummaryRow) {
      const summaryRow = this.calculateSummaryRow(axis, data)
      processedData.push(summaryRow)
    }

    return { processedAxis, processedData }
  }

  init(axis: Array<ChartAxis>, data: Array<ChartData>, options?: { showSummaryRow?: boolean }) {
    const addSummaryRow = options?.showSummaryRow !== false
    const { processedAxis, processedData } = this.processData(axis, data, addSummaryRow)
    
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
