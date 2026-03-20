import { BaseChart, type ChartAxis, type ChartData } from '@/views/chat/component/BaseChart.ts'
import { TableSheet, TableDataCell, type S2Options, type S2DataConfig, type S2MountContainer } from '@antv/s2'
import { debounce } from 'lodash-es'

/** 自定义数据单元格：序号列居中，其余列文字居左、数字居右 */
function isNumericValue(value: unknown): boolean {
  if (value === null || value === undefined || value === '') return false
  if (typeof value === 'number' && !Number.isNaN(value)) return true
  const s = String(value).trim()
  // 支持千分位（如 1,099）、百分号（如 3.74%）等格式，去掉逗号与末尾 % 后再判断
  const normalized = s.replace(/,/g, '').replace(/%\s*$/, '')
  if (/^-?\d+(\.\d+)?$/.test(normalized)) return true
  return false
}

class AlignTableDataCell extends TableDataCell {
  getTextStyle() {
    const textStyle = super.getTextStyle()
    const valueField = this.meta?.valueField
    if (valueField === '__index__') {
      return { ...textStyle, textAlign: 'center' as const }
    }
    const value = this.meta?.fieldValue
    return {
      ...textStyle,
      textAlign: isNumericValue(value) ? ('right' as const) : ('left' as const),
    }
  }
}

<<<<<<< HEAD
const TABLE_HEADER_HEIGHT = 40
const TABLE_ROW_HEIGHT = 32
const TABLE_MAX_HEIGHT = 360


export class Table extends BaseChart {
  table?: TableSheet = undefined

  container: S2MountContainer | null = null

  debounceRender: any

  resizeObserver: ResizeObserver

  /** 按内容计算出的表格高度，resize 时保持不随父容器拉高 */
  private tableHeight: number = TABLE_MAX_HEIGHT

  constructor(id: string) {
    super(id, 'table')
    this.container = document.getElementById(id)

    this.debounceRender = debounce(async (width?: number) => {
      if (this.table) {
        this.table.changeSheetSize(width, this.tableHeight)
        await this.table.render(false)
      }
    }, 200)

    this.resizeObserver = new ResizeObserver(([entry] = []) => {
      const [size] = entry.borderBoxSize || []
      this.debounceRender(size.inlineSize)
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

  /**
   * 判断列是否为「数值编码的分类列」，不应参与汇总求和。
   * 如：注册状态 0/1、境内境外 0/1、与国有企业关系 1/2/3/4、合并标志 1/2 等。
   */
  private isNumericCodedCategoryColumn(col: ChartAxis, data: Array<ChartData>): boolean {
    const values = data.map(row => row[col.value]).filter(v => this.isNumericField(v))
    if (values.length === 0) return false

    const uniqueCount = new Set(values.map(v => String(Number(v)))).size
    const colName = (col.name || col.value || '').toLowerCase()

    // 列名包含类型/状态/标志/关系等，视为分类列
    const categoryKeywords = ['状态', '标志', '关系', '境内', '境外', '合并', '类型', '注册', '出资']
    if (categoryKeywords.some(kw => colName.includes(kw.toLowerCase()))) {
      return true
    }

    // 列名包含金额/税费/收入等，视为指标列，应参与汇总
    const metricKeywords = ['税费', '金额', '亿元', '万元', '收入', '成本', '利润', '资产', '负债']
    if (metricKeywords.some(kw => colName.includes(kw))) {
      return false
    }

    // 唯一值数量在 2~15 之间，多为编码分类（0/1、1/2/3/4 等），不汇总
    if (uniqueCount >= 2 && uniqueCount <= 15) {
      return true
    }

    return false
  }

  // 计算汇总行数据（数值编码的分类列不参与求和）
  private calculateSummaryRow(axis: Array<ChartAxis>, data: Array<ChartData>): ChartData {
    const summaryRow: ChartData = {
      __index__: '汇总',
    }
    
    axis.forEach((col) => {
      if (this.isNumericCodedCategoryColumn(col, data)) {
        summaryRow[col.value] = '-'
        return
      }

      const values = data.map(row => row[col.value]).filter(v => this.isNumericField(v))
      
      if (values.length > 0) {
        const sum = values.reduce((acc, val) => acc + Number(val), 0)
        summaryRow[col.value] = this.formatNumber(sum)
      } else {
        summaryRow[col.value] = ''
      }
    })
    
    return summaryRow
  }

  /** 按所有数据列去重，保留首次出现的行 */
  private deduplicateRows(axis: Array<ChartAxis>, data: Array<ChartData>): Array<ChartData> {
    if (!data?.length || !axis?.length) return data ?? []
    const seen = new Set<string>()
    return data.filter((row) => {
      const key = axis.map((a) => String(row[a.value] ?? '')).join('\u0001')
      if (seen.has(key)) return false
      seen.add(key)
      return true
    })
  }

  // 处理数据：按行去重后添加序号列、格式化数值，可选添加汇总行（构成类问题不汇总）
  private processData(
    axis: Array<ChartAxis>,
    data: Array<ChartData>,
    addSummaryRow: boolean = true
  ): { processedAxis: Array<ChartAxis>; processedData: Array<ChartData> } {
    if (!data || data.length === 0) {
      return { processedAxis: axis, processedData: [] }
    }

    const dataToProcess = this.deduplicateRows(axis, data)

    // 添加序号列
    const indexAxis: ChartAxis = {
      name: '序号',
      value: '__index__',
    }
    const processedAxis = [indexAxis, ...axis]

    // 处理数据：添加序号和格式化数值（汇总行基于去重后的数据计算）
    const processedData = dataToProcess.map((row, index) => {
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
      const summaryRow = this.calculateSummaryRow(axis, dataToProcess)
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

    const tableContentHeight = TABLE_HEADER_HEIGHT + processedData.length * TABLE_ROW_HEIGHT
    this.tableHeight = Math.min(TABLE_MAX_HEIGHT, Math.max(tableContentHeight, TABLE_HEADER_HEIGHT + TABLE_ROW_HEIGHT))

    const s2Options: S2Options = {
      width: 600,
      height: this.tableHeight,
      placeholder: {
        cell: '-',
        empty: {
          icon: 'Empty',
          description: 'No Data',
        },
      },
      dataCell: (viewMeta: any, spreadsheet: any) =>
        new AlignTableDataCell(viewMeta, spreadsheet),
    }

    if (this.container) {
      this.table = new TableSheet(this.container, s2DataConfig, s2Options)
      ;(this.container as HTMLElement).style.height = `${this.tableHeight}px`
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
