import Papa from 'papaparse'
import type {
  CellValue,
  DataColumn,
  DataRow,
  DataSet,
  Point,
} from '../types'

const NUMERIC_THRESHOLD = 0.7

export const SERIES_COLORS = [
  '#e15a35',
  '#0f766e',
  '#d49a22',
  '#315e9f',
  '#8a5a9e',
  '#b73f64',
  '#537b2f',
  '#4c6570',
]

function makeId(prefix: string) {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

function normalizeHeader(value: string, index: number) {
  const cleaned = value.trim().replace(/\s+/g, ' ')
  return cleaned || `Column ${index + 1}`
}

function uniqueHeaders(values: string[]) {
  const counts = new Map<string, number>()
  return values.map((value, index) => {
    const base = normalizeHeader(value, index)
    const count = counts.get(base) ?? 0
    counts.set(base, count + 1)
    return count === 0 ? base : `${base} (${count + 1})`
  })
}

function parseNumber(value: CellValue) {
  if (typeof value === 'number') return Number.isFinite(value) ? value : null
  if (value === null) return null
  const normalized = value.trim().replace(/,/g, '')
  if (!normalized) return null
  const parsed = Number(normalized)
  return Number.isFinite(parsed) ? parsed : null
}

function numericRatio(values: CellValue[]) {
  const populated = values.filter((value) => value !== null && value !== '')
  if (populated.length === 0) return 0
  return populated.filter((value) => parseNumber(value) !== null).length / populated.length
}

function looksLikeHeader(firstRow: CellValue[], secondRow: CellValue[]) {
  if (!secondRow.length) return false
  const firstNumeric = numericRatio(firstRow)
  const secondNumeric = numericRatio(secondRow)
  return firstNumeric < 0.45 && secondNumeric >= 0.55
}

function splitWhitespace(text: string) {
  return text
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter((line) => line && !line.startsWith('#'))
    .map((line) => line.split(/\s+/))
}

function parseDelimited(text: string) {
  const parsed = Papa.parse<string[]>(text, {
    header: false,
    skipEmptyLines: 'greedy',
    dynamicTyping: false,
  })

  const rows = parsed.data
    .map((row) => row.map((cell) => String(cell).trim()))
    .filter((row) => row.some(Boolean))

  if (rows.length < 2) return rows

  const widest = Math.max(...rows.map((row) => row.length))
  if (widest > 1) return rows

  const whitespaceRows = splitWhitespace(text)
  return whitespaceRows.length > rows.length ? whitespaceRows : rows
}

function parseJson(text: string): CellValue[][] {
  const value: unknown = JSON.parse(text)
  if (!Array.isArray(value) || value.length === 0) {
    throw new Error('JSON 必须是至少包含一条记录的对象数组或二维数组。')
  }

  if (Array.isArray(value[0])) {
    return (value as unknown[][]).map((row) =>
      row.map((cell) =>
        typeof cell === 'number' || typeof cell === 'string' ? cell : null,
      ),
    )
  }

  const records = value as Record<string, unknown>[]
  const keys = Array.from(
    new Set(records.flatMap((record) => Object.keys(record))),
  )
  return [
    keys,
    ...records.map((record) =>
      keys.map((key) => {
        const cell = record[key]
        return typeof cell === 'number' || typeof cell === 'string' ? cell : null
      }),
    ),
  ]
}

function tableToDataset(
  table: CellValue[][],
  name: string,
  fileName: string,
): DataSet {
  if (table.length < 2) {
    throw new Error('文件里没有足够的数据行。')
  }

  const firstRow = table[0]
  const secondRow = table[1]
  const hasHeader = looksLikeHeader(firstRow, secondRow)
  const width = Math.max(...table.map((row) => row.length))

  if (width === 0) {
    throw new Error('没有识别到数据列。')
  }

  const rawHeaders = hasHeader
    ? Array.from({ length: width }, (_, index) => String(firstRow[index] ?? ''))
    : Array.from({ length: width }, (_, index) => `Column ${index + 1}`)
  const labels = uniqueHeaders(rawHeaders)
  const keys = labels.map((_, index) => `c${index + 1}`)
  const body = hasHeader ? table.slice(1) : table

  const columns: DataColumn[] = keys.map((key, index) => {
    const values = body.map((row) => row[index] ?? null)
    const ratio = numericRatio(values)
    return {
      key,
      label: labels[index],
      type: ratio >= NUMERIC_THRESHOLD ? 'number' : 'string',
      missing: values.filter((value) => value === null || value === '').length,
    }
  })

  const rows: DataRow[] = body.map((row) => {
    const record: DataRow = {}
    keys.forEach((key, index) => {
      const raw = row[index] ?? null
      const column = columns[index]
      record[key] = column.type === 'number' ? parseNumber(raw) : raw
    })
    return record
  })

  const numericColumns = columns.filter((column) => column.type === 'number')
  if (numericColumns.length === 0) {
    throw new Error('没有找到可绘制的数值列。')
  }

  return {
    id: makeId('dataset'),
    name,
    fileName,
    importedAt: new Date().toISOString(),
    columns,
    rows,
    xColumn: numericColumns[0].key,
    yColumns: numericColumns.slice(1, 3).map((column) => column.key),
    visible: true,
  }
}

export async function parseDataFile(file: File): Promise<DataSet> {
  const text = await file.text()
  const extension = file.name.split('.').pop()?.toLowerCase()
  const table =
    extension === 'json'
      ? parseJson(text)
      : parseDelimited(text)

  return tableToDataset(
    table,
    file.name.replace(/\.[^.]+$/, ''),
    file.name,
  )
}

export function getNumericPoints(
  dataset: DataSet,
  xKey: string,
  yKey: string,
): Point[] {
  return dataset.rows.flatMap((row) => {
    const x = parseNumber(row[xKey])
    const y = parseNumber(row[yKey])
    return x === null || y === null ? [] : [{ x, y }]
  })
}

export function getColumnLabel(dataset: DataSet, key: string) {
  return dataset.columns.find((column) => column.key === key)?.label ?? key
}

export function getSeriesColor(dataset: DataSet, columnKey: string) {
  const plotColumns = dataset.columns.filter(
    (column) =>
      column.type === 'number' && column.key !== dataset.xColumn,
  )
  const index = plotColumns.findIndex((column) => column.key === columnKey)
  return SERIES_COLORS[Math.max(index, 0) % SERIES_COLORS.length]
}

export function downsamplePoints(points: Point[], maxPoints = 6000): Point[] {
  if (points.length <= maxPoints) return points

  const bucketSize = points.length / maxPoints
  const result: Point[] = []
  for (let bucket = 0; bucket < maxPoints; bucket += 1) {
    const start = Math.floor(bucket * bucketSize)
    const end = Math.min(points.length, Math.floor((bucket + 1) * bucketSize))
    let minPoint = points[start]
    let maxPoint = points[start]
    for (let index = start + 1; index < end; index += 1) {
      if (points[index].y < minPoint.y) minPoint = points[index]
      if (points[index].y > maxPoint.y) maxPoint = points[index]
    }
    if (minPoint.x <= maxPoint.x) result.push(minPoint, maxPoint)
    else result.push(maxPoint, minPoint)
  }
  return result
}

export function createSampleDataset(): DataSet {
  const rows: DataRow[] = Array.from({ length: 241 }, (_, index) => {
    const time = index * 0.5
    const transient = Math.exp(-time / 48)
    const pressure =
      15.5 + 0.65 * transient * Math.sin(time / 6.2) + 0.08 * Math.sin(time / 2.4)
    const temperature =
      286 + 13.5 * (1 - Math.exp(-time / 38)) + 1.1 * Math.sin(time / 8.5)
    const flow =
      100 + 17 * Math.exp(-time / 22) * Math.sin(time / 5.4) - 0.026 * time
    return {
      c1: time,
      c2: pressure,
      c3: temperature,
      c4: flow,
    }
  })

  return {
    id: makeId('dataset'),
    name: '稳压器瞬态示例',
    fileName: 'sample-transient.csv',
    importedAt: new Date().toISOString(),
    isSample: true,
    columns: [
      { key: 'c1', label: 'Time (s)', type: 'number', missing: 0 },
      { key: 'c2', label: 'Pressure (MPa)', type: 'number', missing: 0 },
      { key: 'c3', label: 'Temperature (C)', type: 'number', missing: 0 },
      { key: 'c4', label: 'Mass flow (kg/s)', type: 'number', missing: 0 },
    ],
    rows,
    xColumn: 'c1',
    yColumns: ['c2', 'c3', 'c4'],
    visible: true,
  }
}

export function downloadText(
  content: string,
  filename: string,
  type = 'text/plain;charset=utf-8',
) {
  const blob = new Blob([content], { type })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement('a')
  anchor.href = url
  anchor.download = filename
  anchor.click()
  URL.revokeObjectURL(url)
}

export function datasetToCsv(dataset: DataSet, delimiter = ',') {
  const selectedKeys = [
    dataset.xColumn,
    ...dataset.yColumns.filter((key) => key !== dataset.xColumn),
  ]
  const header = selectedKeys.map((key) => getColumnLabel(dataset, key))
  const body = dataset.rows.map((row) =>
    selectedKeys.map((key) => row[key] ?? ''),
  )
  return Papa.unparse([header, ...body], { delimiter })
}
