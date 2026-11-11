export type CellValue = number | string | null

export type ColumnType = 'number' | 'string'

export interface DataColumn {
  key: string
  label: string
  type: ColumnType
  missing: number
}

export interface DataRow {
  [key: string]: CellValue
}

export interface DataSet {
  id: string
  name: string
  fileName: string
  importedAt: string
  isSample?: boolean
  columns: DataColumn[]
  rows: DataRow[]
  xColumn: string
  yColumns: string[]
  visible: boolean
}

export interface Point {
  x: number
  y: number
}

export type DerivedKind =
  | 'smooth'
  | 'derivative'
  | 'integral'
  | 'normalize'
  | 'fft'
  | 'regression'

export interface DerivedSeries {
  id: string
  sourceDatasetId: string
  sourceColumn: string
  label: string
  kind: DerivedKind
  color: string
  points: Point[]
  visible: boolean
}

export interface ChartSettings {
  title: string
  xLabel: string
  yLabel: string
  xScale: 'linear' | 'log'
  yScale: 'linear' | 'log'
  showGrid: boolean
  showLegend: boolean
  showMarkers: boolean
  lineWidth: number
  markerSize: number
  smoothLines: boolean
}

export interface StatsSummary {
  count: number
  min: number
  max: number
  mean: number
  median: number
  stdDev: number
  range: number
}

export interface RegressionSummary {
  slope: number
  intercept: number
  rSquared: number
  equation: string
}

export interface ProjectState {
  version: 1
  name: string
  savedAt: string
  datasets: DataSet[]
  derivedSeries: DerivedSeries[]
  activeDatasetId: string | null
  chart: ChartSettings
}
