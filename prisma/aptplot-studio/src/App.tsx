import {
  useEffect,
  useRef,
  useState,
} from 'react'
import type { ChangeEvent, DragEvent } from 'react'
import Plotly from 'plotly.js-dist-min'
import {
  AudioWaveform,
  ChartLine,
  ChevronDown,
  Database,
  Download,
  Eye,
  EyeOff,
  FilePlus2,
  FileSpreadsheet,
  FolderOpen,
  Gauge,
  Image,
  LineChart,
  LoaderCircle,
  PanelLeft,
  Plus,
  RotateCcw,
  Save,
  Settings2,
  Sigma,
  SlidersHorizontal,
  Table2,
  Trash2,
  TrendingUp,
  Upload,
  Waves,
  X,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { PlotCanvas } from './components/PlotCanvas'
import { RstpltImportDialog } from './components/RstpltImportDialog'
import {
  SERIES_COLORS,
  createSampleDataset,
  datasetToCsv,
  downloadText,
  getColumnLabel,
  getNumericPoints,
  getSeriesColor,
  parseDataFile,
} from './lib/data'
import {
  isRstpltFilename,
  loadRstpltDataset,
  scanRstpltFile,
  sniffRstpltFile,
} from './lib/rstplt'
import type { RstpltCatalog } from './lib/rstplt'
import {
  derivativePoints,
  fftMagnitude,
  integralPoints,
  linearRegression,
  normalizePoints,
  smoothPoints,
  summarize,
} from './lib/analysis'
import type {
  ChartSettings,
  DataSet,
  DerivedKind,
  DerivedSeries,
  ProjectState,
} from './types'
import './App.css'

const STORAGE_KEY = 'aptplot-workbench-project-v1'

const DEFAULT_CHART: ChartSettings = {
  title: '稳压器瞬态响应',
  xLabel: 'Time (s)',
  yLabel: 'Measured value',
  xScale: 'linear',
  yScale: 'linear',
  showGrid: true,
  showLegend: true,
  showMarkers: false,
  lineWidth: 2,
  markerSize: 5,
  smoothLines: false,
}

const ANALYSIS_ACTIONS: {
  kind: DerivedKind
  label: string
  icon: LucideIcon
}[] = [
  { kind: 'smooth', label: '移动平均', icon: Waves },
  { kind: 'derivative', label: '求导数', icon: TrendingUp },
  { kind: 'integral', label: '累计积分', icon: Sigma },
  { kind: 'normalize', label: '归一化', icon: Gauge },
  { kind: 'fft', label: 'FFT 频谱', icon: AudioWaveform },
  { kind: 'regression', label: '线性回归', icon: ChartLine },
]

const ANALYSIS_LABELS: Record<DerivedKind, string> = {
  smooth: '移动平均',
  derivative: '导数',
  integral: '积分',
  normalize: '归一化',
  fft: 'FFT',
  regression: '线性回归',
}

interface Notice {
  message: string
  tone: 'info' | 'success' | 'error'
}

interface RstpltImportState {
  file: File
  catalog: RstpltCatalog
}

interface ImportTask {
  fileName: string
  phase: 'scan' | 'load'
  progress: number
}

function createProject(): ProjectState {
  const sample = createSampleDataset()
  return {
    version: 1,
    name: '未命名项目',
    savedAt: new Date().toISOString(),
    datasets: [sample],
    derivedSeries: [],
    activeDatasetId: sample.id,
    chart: DEFAULT_CHART,
  }
}

function loadInitialProject(): ProjectState {
  try {
    const stored = localStorage.getItem(STORAGE_KEY)
    if (!stored) return createProject()
    const project = JSON.parse(stored) as ProjectState
    if (
      project.version !== 1 ||
      !Array.isArray(project.datasets) ||
      project.datasets.length === 0
    ) {
      return createProject()
    }
    return project
  } catch {
    return createProject()
  }
}

function safeFilename(value: string) {
  return (
    value
      .trim()
      .replace(/[\\/:*?"<>|]+/g, '-')
      .replace(/\s+/g, '-')
      .slice(0, 80) || 'aptplot-project'
  )
}

function formatNumber(value: number) {
  if (!Number.isFinite(value)) return '—'
  if (value === 0) return '0'
  const absolute = Math.abs(value)
  if (absolute >= 1e5 || absolute < 1e-3) return value.toExponential(3)
  return value.toLocaleString('zh-CN', { maximumFractionDigits: 5 })
}

function createDerivedId() {
  return `derived-${Date.now()}-${Math.random().toString(36).slice(2, 7)}`
}

function isInitialSampleDataset(dataset: DataSet) {
  return dataset.isSample === true || dataset.fileName === 'sample-transient.csv'
}

function mergeImportedDatasets(
  current: ProjectState,
  imported: DataSet[],
) {
  const onlyInitialSample =
    current.datasets.length === 1 &&
    isInitialSampleDataset(current.datasets[0]) &&
    current.derivedSeries.length === 0

  return onlyInitialSample
    ? imported
    : [...current.datasets, ...imported]
}

function App() {
  const [project, setProject] = useState<ProjectState>(loadInitialProject)
  const [notice, setNotice] = useState<Notice | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [rightTab, setRightTab] = useState<'chart' | 'analysis'>('chart')
  const [bottomTab, setBottomTab] = useState<'table' | 'stats'>('table')
  const [smoothingWindow, setSmoothingWindow] = useState(7)
  const [mobilePanel, setMobilePanel] = useState<'data' | 'settings' | null>(
    null,
  )
  const [isExportOpen, setIsExportOpen] = useState(false)
  const [rstpltImport, setRstpltImport] =
    useState<RstpltImportState | null>(null)
  const [importTask, setImportTask] = useState<ImportTask | null>(null)
  const plotRef = useRef<HTMLDivElement>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const projectInputRef = useRef<HTMLInputElement>(null)
  const noticeTimerRef = useRef<number | null>(null)
  const rstpltQueueRef = useRef<File[]>([])
  const rstpltOpeningRef = useRef(false)

  const activeDataset =
    project.datasets.find((dataset) => dataset.id === project.activeDatasetId) ??
    project.datasets[0] ??
    null

  const primaryColumn = activeDataset?.yColumns[0] ?? null
  const primaryPoints =
    activeDataset && primaryColumn
      ? getNumericPoints(
          activeDataset,
          activeDataset.xColumn,
          primaryColumn,
        )
      : []
  const primaryStats = summarize(primaryPoints)
  const visibleSeriesCount = project.datasets.reduce(
    (count, dataset) =>
      count + (dataset.visible ? dataset.yColumns.length : 0),
    project.derivedSeries.filter((series) => series.visible).length,
  )
  const totalRows = project.datasets.reduce(
    (count, dataset) => count + dataset.rows.length,
    0,
  )

  function notify(message: string, tone: Notice['tone'] = 'info') {
    setNotice({ message, tone })
    if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
    noticeTimerRef.current = window.setTimeout(() => setNotice(null), 3200)
  }

  useEffect(() => {
    const timer = window.setTimeout(() => {
      try {
        const serialized = JSON.stringify(project)
        if (serialized.length < 4_000_000) {
          localStorage.setItem(STORAGE_KEY, serialized)
        }
      } catch {
        // Large projects still work in memory and can be saved as files.
      }
    }, 500)
    return () => window.clearTimeout(timer)
  }, [project])

  useEffect(
    () => () => {
      if (noticeTimerRef.current) window.clearTimeout(noticeTimerRef.current)
    },
    [],
  )

  function updateDataset(
    datasetId: string,
    updater: (dataset: DataSet) => DataSet,
  ) {
    setProject((current) => ({
      ...current,
      datasets: current.datasets.map((dataset) =>
        dataset.id === datasetId ? updater(dataset) : dataset,
      ),
    }))
  }

  function updateChart(update: Partial<ChartSettings>) {
    setProject((current) => ({
      ...current,
      chart: { ...current.chart, ...update },
    }))
  }

  async function importRegularFiles(files: File[]) {
    const imported = await Promise.all(files.map(parseDataFile))
    const latest = imported[imported.length - 1]
    setProject((current) => ({
      ...current,
      datasets: mergeImportedDatasets(current, imported),
      activeDatasetId: latest.id,
      chart: {
        ...current.chart,
        title: latest.name,
        xLabel: getColumnLabel(latest, latest.xColumn),
        yLabel:
          latest.yColumns.length === 1
            ? getColumnLabel(latest, latest.yColumns[0])
            : 'Value',
      },
    }))
    notify(
      `已导入 ${imported.length} 个文件，共 ${imported.reduce(
        (count, dataset) => count + dataset.rows.length,
        0,
      )} 行`,
      'success',
    )
  }

  async function importFiles(files: File[]) {
    const validFiles = files.filter((file) => file.size > 0)
    if (validFiles.length === 0) return

    const regularFiles: File[] = []
    const rstpltFiles: File[] = []
    for (const file of validFiles) {
      const extension = file.name.split('.').pop()?.toLowerCase()
      if (isRstpltFilename(file.name)) {
        rstpltFiles.push(file)
      } else if (
        ['csv', 'tsv', 'txt', 'dat', 'json'].includes(extension ?? '')
      ) {
        regularFiles.push(file)
      } else if (await sniffRstpltFile(file)) {
        rstpltFiles.push(file)
      } else {
        regularFiles.push(file)
      }
    }

    if (regularFiles.length > 0) {
      try {
        await importRegularFiles(regularFiles)
      } catch (error) {
        notify(
          error instanceof Error ? error.message : '文件解析失败。',
          'error',
        )
      }
    }

    if (rstpltFiles.length > 0) {
      rstpltQueueRef.current.push(...rstpltFiles)
      void startNextRstpltImport()
    }
  }

  async function startNextRstpltImport() {
    if (rstpltOpeningRef.current) return
    const file = rstpltQueueRef.current.shift()
    if (!file) return

    rstpltOpeningRef.current = true
    setImportTask({ fileName: file.name, phase: 'scan', progress: 0 })
    try {
      const catalog = await scanRstpltFile(file, (done, total) => {
        setImportTask({
          fileName: file.name,
          phase: 'scan',
          progress: total > 0 ? Math.min(1, done / total) : 0,
        })
      })
      if (!catalog.variables.some((variable) => !variable.isTime)) {
        throw new Error('RSTPLT 文件中没有 time 之外的可绘制变量。')
      }
      setRstpltImport({ file, catalog })
    } catch (error) {
      notify(
        error instanceof Error ? error.message : 'RSTPLT 文件扫描失败。',
        'error',
      )
      rstpltOpeningRef.current = false
      window.setTimeout(() => void startNextRstpltImport(), 80)
    } finally {
      setImportTask(null)
    }
  }

  function finishRstpltImport() {
    setRstpltImport(null)
    setImportTask(null)
    rstpltOpeningRef.current = false
    window.setTimeout(() => void startNextRstpltImport(), 0)
  }

  async function confirmRstpltImport(variableIds: string[]) {
    const pending = rstpltImport
    if (!pending) return

    setImportTask({
      fileName: pending.file.name,
      phase: 'load',
      progress: 0,
    })
    try {
      const dataset = await loadRstpltDataset(
        pending.file,
        pending.catalog,
        variableIds,
        (done, total) => {
          setImportTask({
            fileName: pending.file.name,
            phase: 'load',
            progress: total > 0 ? Math.min(1, done / total) : 0,
          })
        },
      )
      setProject((current) => ({
        ...current,
        datasets: mergeImportedDatasets(current, [dataset]),
        activeDatasetId: dataset.id,
        chart: {
          ...current.chart,
          title: dataset.name,
          xLabel: getColumnLabel(dataset, dataset.xColumn),
          yLabel:
            dataset.yColumns.length === 1
              ? getColumnLabel(dataset, dataset.yColumns[0])
              : 'Value',
        },
      }))
      notify(
        `已从 RSTPLT 读取 ${dataset.rows.toLocaleString(
          'zh-CN',
        )} 个时间点、${dataset.yColumns.length} 条曲线。`,
        'success',
      )
      finishRstpltImport()
    } catch (error) {
      setImportTask(null)
      notify(
        error instanceof Error ? error.message : 'RSTPLT 数据读取失败。',
        'error',
      )
    }
  }

  function handleFileInput(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? [])
    event.target.value = ''
    void importFiles(files)
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    setIsDragging(false)
    void importFiles(Array.from(event.dataTransfer.files))
  }

  function selectDataset(datasetId: string) {
    setProject((current) => ({ ...current, activeDatasetId: datasetId }))
    setMobilePanel(null)
  }

  function toggleDataset(datasetId: string) {
    updateDataset(datasetId, (dataset) => ({
      ...dataset,
      visible: !dataset.visible,
    }))
  }

  function removeDataset(datasetId: string) {
    setProject((current) => {
      const datasets = current.datasets.filter(
        (dataset) => dataset.id !== datasetId,
      )
      return {
        ...current,
        datasets,
        derivedSeries: current.derivedSeries.filter(
          (series) => series.sourceDatasetId !== datasetId,
        ),
        activeDatasetId:
          current.activeDatasetId === datasetId
            ? (datasets[0]?.id ?? null)
            : current.activeDatasetId,
      }
    })
    notify('数据源已移除。')
  }

  function setXColumn(columnKey: string) {
    if (!activeDataset) return
    updateDataset(activeDataset.id, (dataset) => {
      const remainingY = dataset.yColumns.filter((key) => key !== columnKey)
      const fallbackY = dataset.columns.find(
        (column) =>
          column.type === 'number' && column.key !== columnKey,
      )?.key
      return {
        ...dataset,
        xColumn: columnKey,
        yColumns: remainingY.length
          ? remainingY
          : fallbackY
            ? [fallbackY]
            : [],
      }
    })
    updateChart({ xLabel: getColumnLabel(activeDataset, columnKey) })
  }

  function toggleYColumn(columnKey: string) {
    if (!activeDataset) return
    updateDataset(activeDataset.id, (dataset) => {
      const selected = dataset.yColumns.includes(columnKey)
      if (selected && dataset.yColumns.length === 1) return dataset
      return {
        ...dataset,
        yColumns: selected
          ? dataset.yColumns.filter((key) => key !== columnKey)
          : [...dataset.yColumns, columnKey],
      }
    })
  }

  function resetView() {
    if (!plotRef.current) return
    void Plotly.relayout(plotRef.current, {
      'xaxis.autorange': true,
      'yaxis.autorange': true,
    })
  }

  async function exportImage(format: 'png' | 'svg') {
    if (!plotRef.current) return
    try {
      await Plotly.downloadImage(plotRef.current, {
        format,
        filename: safeFilename(project.chart.title),
        width: 1800,
        height: 1000,
        scale: format === 'png' ? 1.5 : 1,
      })
      setIsExportOpen(false)
      notify(`已导出 ${format.toUpperCase()} 图像。`, 'success')
    } catch {
      notify('图像导出失败。', 'error')
    }
  }

  function exportCsv(delimiter: ',' | '\t') {
    if (!activeDataset) {
      notify('请先选择数据源。', 'error')
      return
    }
    const extension = delimiter === ',' ? 'csv' : 'tsv'
    downloadText(
      datasetToCsv(activeDataset, delimiter),
      `${safeFilename(activeDataset.name)}.${extension}`,
      delimiter === ','
        ? 'text/csv;charset=utf-8'
        : 'text/tab-separated-values;charset=utf-8',
    )
    setIsExportOpen(false)
    notify(`已导出 ${extension.toUpperCase()} 数据。`, 'success')
  }

  function saveProjectFile() {
    const snapshot: ProjectState = {
      ...project,
      savedAt: new Date().toISOString(),
    }
    downloadText(
      JSON.stringify(snapshot, null, 2),
      `${safeFilename(project.name)}.aptproj.json`,
      'application/json;charset=utf-8',
    )
    notify('项目文件已保存。', 'success')
  }

  useEffect(() => {
    function handleKeyboard(event: KeyboardEvent) {
      if (!(event.ctrlKey || event.metaKey)) return
      if (event.key.toLowerCase() === 'o') {
        event.preventDefault()
        fileInputRef.current?.click()
      }
      if (event.key.toLowerCase() === 's') {
        event.preventDefault()
        saveProjectFile()
      }
    }
    window.addEventListener('keydown', handleKeyboard)
    return () => window.removeEventListener('keydown', handleKeyboard)
  })

  function openProjectFile(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return

    void file
      .text()
      .then((text) => {
        const parsed = JSON.parse(text) as ProjectState
        if (
          parsed.version !== 1 ||
          !Array.isArray(parsed.datasets) ||
          !parsed.chart
        ) {
          throw new Error('不是有效的 AptPlot Workbench 项目文件。')
        }
        setProject(parsed)
        notify('项目已打开。', 'success')
      })
      .catch((error) => {
        notify(
          error instanceof Error ? error.message : '项目文件无法读取。',
          'error',
        )
      })
  }

  function addSampleDataset() {
    const sample = createSampleDataset()
    setProject((current) => ({
      ...current,
      datasets: [...current.datasets, sample],
      activeDatasetId: sample.id,
    }))
    notify('已添加一组瞬态示例数据。', 'success')
  }

  function runAnalysis(kind: DerivedKind) {
    if (!activeDataset || !primaryColumn) {
      notify('请先选择一个数据列。', 'error')
      return
    }
    if (primaryPoints.length < 2) {
      notify('当前数据列没有足够的有效数值。', 'error')
      return
    }

    let points = primaryPoints
    let suffix = ANALYSIS_LABELS[kind]
    try {
      if (kind === 'smooth') points = smoothPoints(primaryPoints, smoothingWindow)
      if (kind === 'derivative') points = derivativePoints(primaryPoints)
      if (kind === 'integral') points = integralPoints(primaryPoints)
      if (kind === 'normalize') points = normalizePoints(primaryPoints)
      if (kind === 'fft') points = fftMagnitude(primaryPoints)
      if (kind === 'regression') {
        const regression = linearRegression(primaryPoints)
        points = regression.points
        suffix = `线性回归  R²=${regression.summary.rSquared.toFixed(4)}`
      }
    } catch (error) {
      notify(error instanceof Error ? error.message : '分析失败。', 'error')
      return
    }
    if (points.length === 0) {
      notify('处理后的数据点不足，请检查横轴是否存在重复值。', 'error')
      return
    }

    const sourceLabel = getColumnLabel(activeDataset, primaryColumn)
    const series: DerivedSeries = {
      id: createDerivedId(),
      sourceDatasetId: activeDataset.id,
      sourceColumn: primaryColumn,
      label: `${sourceLabel} / ${suffix}`,
      kind,
      color:
        SERIES_COLORS[
          (project.derivedSeries.length + 2) % SERIES_COLORS.length
        ],
      points,
      visible: true,
    }
    setProject((current) => ({
      ...current,
      derivedSeries: [...current.derivedSeries, series],
    }))
    setRightTab('analysis')
    notify(`${ANALYSIS_LABELS[kind]}结果已加入图表。`, 'success')
  }

  function toggleDerivedSeries(seriesId: string) {
    setProject((current) => ({
      ...current,
      derivedSeries: current.derivedSeries.map((series) =>
        series.id === seriesId
          ? { ...series, visible: !series.visible }
          : series,
      ),
    }))
  }

  function removeDerivedSeries(seriesId: string) {
    setProject((current) => ({
      ...current,
      derivedSeries: current.derivedSeries.filter(
        (series) => series.id !== seriesId,
      ),
    }))
  }

  const numericColumns =
    activeDataset?.columns.filter((column) => column.type === 'number') ?? []
  const previewColumns =
    activeDataset?.columns.filter(
      (column) =>
        column.key === activeDataset.xColumn ||
        activeDataset.yColumns.includes(column.key),
    ) ?? []

  return (
    <div
      className={`app-shell ${isDragging ? 'is-dragging' : ''}`}
      onDragEnter={(event) => {
        event.preventDefault()
        setIsDragging(true)
      }}
      onDragOver={(event) => event.preventDefault()}
      onDragLeave={(event) => {
        if (event.currentTarget === event.target) setIsDragging(false)
      }}
      onDrop={handleDrop}
    >
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <LineChart size={19} strokeWidth={2.2} />
          </span>
          <div>
            <strong>AptPlot Workbench</strong>
            <span>核热工数据分析</span>
          </div>
        </div>

        <div className="top-actions">
          <button
            className="button button-primary"
            type="button"
            aria-label="导入数据"
            onClick={() => fileInputRef.current?.click()}
          >
            <Upload size={15} />
            <span>导入数据</span>
          </button>
          <button
            className="button"
            type="button"
            onClick={() => projectInputRef.current?.click()}
            title="打开项目"
          >
            <FolderOpen size={15} />
            <span className="optional-label">打开</span>
          </button>
          <button
            className="button"
            type="button"
            onClick={saveProjectFile}
            title="保存项目"
          >
            <Save size={15} />
            <span className="optional-label">保存</span>
          </button>
          <div className="export-menu">
            <button
              className="button"
              type="button"
              aria-label="导出"
              onClick={() => setIsExportOpen((open) => !open)}
              aria-expanded={isExportOpen}
            >
              <Download size={15} />
              <span>导出</span>
              <ChevronDown size={13} />
            </button>
            {isExportOpen && (
              <div className="export-popover">
                <button type="button" onClick={() => void exportImage('png')}>
                  <Image size={15} />
                  PNG 图像
                </button>
                <button type="button" onClick={() => void exportImage('svg')}>
                  <Image size={15} />
                  SVG 矢量图
                </button>
                <button type="button" onClick={() => exportCsv(',')}>
                  <FileSpreadsheet size={15} />
                  CSV 数据
                </button>
                <button type="button" onClick={() => exportCsv('\t')}>
                  <FileSpreadsheet size={15} />
                  TSV 数据
                </button>
              </div>
            )}
          </div>
        </div>

        <div className="top-status">
          <span className="status-dot" />
          <span>本地运行</span>
          <button
            className="icon-button mobile-only"
            type="button"
            onClick={() =>
              setMobilePanel((panel) => (panel === 'data' ? null : 'data'))
            }
            title="数据面板"
          >
            <PanelLeft size={18} />
          </button>
          <button
            className="icon-button mobile-only"
            type="button"
            onClick={() =>
              setMobilePanel((panel) =>
                panel === 'settings' ? null : 'settings',
              )
            }
            title="设置面板"
          >
            <SlidersHorizontal size={18} />
          </button>
        </div>
      </header>

      <main className="workspace">
        <aside
          className={`side-panel data-panel ${
            mobilePanel === 'data' ? 'mobile-open' : ''
          }`}
          aria-label="数据源面板"
        >
          <div className="panel-heading">
            <div>
              <span className="section-kicker">工作区</span>
              <h2>数据源</h2>
            </div>
            <div className="heading-actions">
              <button
                className="icon-button"
                type="button"
                onClick={() => fileInputRef.current?.click()}
                title="添加数据文件"
              >
                <Plus size={17} />
              </button>
              <button
                className="icon-button mobile-only"
                type="button"
                onClick={() => setMobilePanel(null)}
                title="关闭面板"
              >
                <X size={17} />
              </button>
            </div>
          </div>

          <div className="dataset-list">
            {project.datasets.map((dataset) => {
              const active = dataset.id === activeDataset?.id
              return (
                <div
                  className={`dataset-item ${active ? 'active' : ''}`}
                  key={dataset.id}
                >
                  <button
                    className="dataset-select"
                    type="button"
                    onClick={() => selectDataset(dataset.id)}
                  >
                    <span className="dataset-icon">
                      <Database size={15} />
                    </span>
                    <span className="dataset-copy">
                      <strong>{dataset.name}</strong>
                      <small>
                        {dataset.rows.length.toLocaleString('zh-CN')} 行 /{' '}
                        {dataset.columns.length} 列
                      </small>
                    </span>
                  </button>
                  <div className="dataset-actions">
                    <button
                      className="icon-button compact"
                      type="button"
                      onClick={() => toggleDataset(dataset.id)}
                      title={dataset.visible ? '隐藏曲线' : '显示曲线'}
                    >
                      {dataset.visible ? (
                        <Eye size={15} />
                      ) : (
                        <EyeOff size={15} />
                      )}
                    </button>
                    <button
                      className="icon-button compact danger"
                      type="button"
                      onClick={() => removeDataset(dataset.id)}
                      title="移除数据源"
                    >
                      <Trash2 size={14} />
                    </button>
                  </div>
                </div>
              )
            })}
          </div>

          {activeDataset && (
            <section className="column-config">
              <div className="section-title">
                <span>曲线选择</span>
                <small>{activeDataset.fileName}</small>
              </div>
              <label className="field">
                <span>横轴</span>
                <select
                  value={activeDataset.xColumn}
                  onChange={(event) => setXColumn(event.target.value)}
                >
                  {numericColumns.map((column) => (
                    <option key={column.key} value={column.key}>
                      {column.label}
                    </option>
                  ))}
                </select>
              </label>
              <div className="column-checks">
                {numericColumns
                  .filter((column) => column.key !== activeDataset.xColumn)
                  .map((column) => {
                    const selected = activeDataset.yColumns.includes(column.key)
                    return (
                      <label
                        className={`column-check ${selected ? 'selected' : ''}`}
                        key={column.key}
                      >
                        <input
                          type="checkbox"
                          checked={selected}
                          onChange={() => toggleYColumn(column.key)}
                        />
                        <span
                          className="series-swatch"
                          style={{
                            backgroundColor: getSeriesColor(
                              activeDataset,
                              column.key,
                            ),
                          }}
                        />
                        <span>{column.label}</span>
                        <small>
                          {column.missing > 0
                            ? `${column.missing} 缺失`
                            : '完整'}
                        </small>
                      </label>
                    )
                  })}
              </div>
            </section>
          )}

          {project.derivedSeries.length > 0 && (
            <section className="derived-list">
              <div className="section-title">
                <span>分析结果</span>
                <small>{project.derivedSeries.length} 条</small>
              </div>
              {project.derivedSeries.map((series) => (
                <div className="derived-item" key={series.id}>
                  <span
                    className="series-swatch"
                    style={{ backgroundColor: series.color }}
                  />
                  <span title={series.label}>{series.label}</span>
                  <button
                    className="icon-button compact"
                    type="button"
                    onClick={() => toggleDerivedSeries(series.id)}
                    title={series.visible ? '隐藏结果' : '显示结果'}
                  >
                    {series.visible ? <Eye size={14} /> : <EyeOff size={14} />}
                  </button>
                  <button
                    className="icon-button compact danger"
                    type="button"
                    onClick={() => removeDerivedSeries(series.id)}
                    title="删除结果"
                  >
                    <Trash2 size={13} />
                  </button>
                </div>
              ))}
            </section>
          )}

          <div className="panel-footer">
            <button
              className="text-button"
              type="button"
              onClick={addSampleDataset}
            >
              <FilePlus2 size={15} />
              添加示例数据
            </button>
            <span>CSV / TXT / JSON / RSTPLT</span>
          </div>
        </aside>

        <section className="plot-workspace">
          <div className="plot-heading">
            <div className="plot-title-copy">
              <span className="section-kicker">当前图表</span>
              <h1>{project.chart.title || '未命名图表'}</h1>
              <p>
                {visibleSeriesCount} 条曲线
                <span aria-hidden="true">/</span>
                {totalRows.toLocaleString('zh-CN')} 个数据行
              </p>
            </div>
            <div className="plot-actions">
              <button
                className="button button-quiet"
                type="button"
                onClick={resetView}
              >
                <RotateCcw size={15} />
                复位视图
              </button>
              <button
                className="button button-quiet"
                type="button"
                onClick={() => void exportImage('png')}
              >
                <Download size={15} />
                PNG
              </button>
            </div>
          </div>

          <div className="plot-stage">
            <PlotCanvas
              plotRef={plotRef}
              datasets={project.datasets}
              derivedSeries={project.derivedSeries}
              settings={project.chart}
            />
            {project.datasets.length === 0 && (
              <div className="empty-plot">
                <Upload size={28} />
                <strong>导入数据开始绘图</strong>
                <button
                  className="button button-primary"
                  type="button"
                  onClick={() => fileInputRef.current?.click()}
                >
                  选择文件
                </button>
              </div>
            )}
          </div>

          <section className="data-preview">
            <div className="preview-heading">
              <div className="tab-list" role="tablist">
                <button
                  className={bottomTab === 'table' ? 'active' : ''}
                  type="button"
                  onClick={() => setBottomTab('table')}
                  role="tab"
                  aria-selected={bottomTab === 'table'}
                >
                  <Table2 size={14} />
                  数据表
                </button>
                <button
                  className={bottomTab === 'stats' ? 'active' : ''}
                  type="button"
                  onClick={() => setBottomTab('stats')}
                  role="tab"
                  aria-selected={bottomTab === 'stats'}
                >
                  <Gauge size={14} />
                  统计
                </button>
              </div>
              <span className="preview-meta">
                {activeDataset
                  ? `${activeDataset.name} / 前 ${Math.min(
                      200,
                      activeDataset.rows.length,
                    )} 行`
                  : '没有数据'}
              </span>
            </div>

            {bottomTab === 'table' && activeDataset && (
              <div
                className="table-wrap"
                tabIndex={0}
                role="region"
                aria-label="数据表，可滚动查看"
              >
                <table>
                  <thead>
                    <tr>
                      <th>#</th>
                      {previewColumns.map((column) => (
                        <th key={column.key}>{column.label}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {activeDataset.rows.slice(0, 200).map((row, index) => (
                      <tr key={`${activeDataset.id}-${index}`}>
                        <td>{index + 1}</td>
                        {previewColumns.map((column) => (
                          <td key={column.key}>
                            {typeof row[column.key] === 'number'
                              ? formatNumber(row[column.key] as number)
                              : String(row[column.key] ?? '')}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}

            {bottomTab === 'stats' && (
              <div className="stats-layout">
                <div className="stats-context">
                  <span className="section-kicker">当前曲线</span>
                  <strong>
                    {activeDataset && primaryColumn
                      ? getColumnLabel(activeDataset, primaryColumn)
                      : '未选择'}
                  </strong>
                  <span>{primaryStats.count} 个有效点</span>
                </div>
                <div className="stat-grid">
                  {[
                    ['最小值', primaryStats.min],
                    ['最大值', primaryStats.max],
                    ['平均值', primaryStats.mean],
                    ['中位数', primaryStats.median],
                    ['标准差', primaryStats.stdDev],
                    ['极差', primaryStats.range],
                  ].map(([label, value]) => (
                    <div className="stat-cell" key={String(label)}>
                      <span>{label}</span>
                      <strong>{formatNumber(Number(value))}</strong>
                    </div>
                  ))}
                </div>
              </div>
            )}
          </section>
        </section>

        <aside
          className={`side-panel inspector-panel ${
            mobilePanel === 'settings' ? 'mobile-open' : ''
          }`}
          aria-label="图表设置面板"
        >
          <div className="panel-heading inspector-heading">
            <div className="tab-list" role="tablist">
              <button
                className={rightTab === 'chart' ? 'active' : ''}
                type="button"
                onClick={() => setRightTab('chart')}
                role="tab"
                aria-selected={rightTab === 'chart'}
              >
                <Settings2 size={14} />
                图表
              </button>
              <button
                className={rightTab === 'analysis' ? 'active' : ''}
                type="button"
                onClick={() => setRightTab('analysis')}
                role="tab"
                aria-selected={rightTab === 'analysis'}
              >
                <ChartLine size={14} />
                分析
              </button>
            </div>
            <button
              className="icon-button mobile-only"
              type="button"
              onClick={() => setMobilePanel(null)}
              title="关闭面板"
            >
              <X size={17} />
            </button>
          </div>

          {rightTab === 'chart' ? (
            <div className="inspector-content">
              <section className="control-section">
                <div className="section-title">
                  <span>标题与坐标</span>
                </div>
                <label className="field">
                  <span>图表标题</span>
                  <input
                    value={project.chart.title}
                    onChange={(event) =>
                      updateChart({ title: event.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>横轴标签</span>
                  <input
                    value={project.chart.xLabel}
                    onChange={(event) =>
                      updateChart({ xLabel: event.target.value })
                    }
                  />
                </label>
                <label className="field">
                  <span>纵轴标签</span>
                  <input
                    value={project.chart.yLabel}
                    onChange={(event) =>
                      updateChart({ yLabel: event.target.value })
                    }
                  />
                </label>
              </section>

              <section className="control-section">
                <div className="section-title">
                  <span>坐标尺度</span>
                </div>
                <div className="control-row">
                  <span>横轴</span>
                  <div className="segmented">
                    {(['linear', 'log'] as const).map((scale) => (
                      <button
                        className={
                          project.chart.xScale === scale ? 'active' : ''
                        }
                        type="button"
                        key={scale}
                        onClick={() => updateChart({ xScale: scale })}
                      >
                        {scale === 'linear' ? '线性' : '对数'}
                      </button>
                    ))}
                  </div>
                </div>
                <div className="control-row">
                  <span>纵轴</span>
                  <div className="segmented">
                    {(['linear', 'log'] as const).map((scale) => (
                      <button
                        className={
                          project.chart.yScale === scale ? 'active' : ''
                        }
                        type="button"
                        key={scale}
                        onClick={() => updateChart({ yScale: scale })}
                      >
                        {scale === 'linear' ? '线性' : '对数'}
                      </button>
                    ))}
                  </div>
                </div>
              </section>

              <section className="control-section">
                <div className="section-title">
                  <span>显示</span>
                </div>
                <label className="toggle-row">
                  <span>显示网格</span>
                  <input
                    type="checkbox"
                    checked={project.chart.showGrid}
                    onChange={(event) =>
                      updateChart({ showGrid: event.target.checked })
                    }
                  />
                  <span className="toggle-track" />
                </label>
                <label className="toggle-row">
                  <span>显示图例</span>
                  <input
                    type="checkbox"
                    checked={project.chart.showLegend}
                    onChange={(event) =>
                      updateChart({ showLegend: event.target.checked })
                    }
                  />
                  <span className="toggle-track" />
                </label>
                <label className="toggle-row">
                  <span>显示数据点</span>
                  <input
                    type="checkbox"
                    checked={project.chart.showMarkers}
                    onChange={(event) =>
                      updateChart({ showMarkers: event.target.checked })
                    }
                  />
                  <span className="toggle-track" />
                </label>
                <label className="toggle-row">
                  <span>平滑曲线</span>
                  <input
                    type="checkbox"
                    checked={project.chart.smoothLines}
                    onChange={(event) =>
                      updateChart({ smoothLines: event.target.checked })
                    }
                  />
                  <span className="toggle-track" />
                </label>
              </section>

              <section className="control-section">
                <div className="section-title">
                  <span>线条</span>
                </div>
                <label className="range-field">
                  <span>
                    线宽
                    <output>{project.chart.lineWidth}px</output>
                  </span>
                  <input
                    type="range"
                    min="1"
                    max="6"
                    step="0.5"
                    value={project.chart.lineWidth}
                    onChange={(event) =>
                      updateChart({ lineWidth: Number(event.target.value) })
                    }
                  />
                </label>
                <label className="range-field">
                  <span>
                    点大小
                    <output>{project.chart.markerSize}px</output>
                  </span>
                  <input
                    type="range"
                    min="2"
                    max="12"
                    step="1"
                    value={project.chart.markerSize}
                    onChange={(event) =>
                      updateChart({ markerSize: Number(event.target.value) })
                    }
                  />
                </label>
              </section>
            </div>
          ) : (
            <div className="inspector-content">
              <section className="control-section analysis-summary">
                <div className="section-title">
                  <span>当前数据</span>
                  <small>{primaryStats.count} 点</small>
                </div>
                <strong>
                  {activeDataset && primaryColumn
                    ? getColumnLabel(activeDataset, primaryColumn)
                    : '未选择曲线'}
                </strong>
                <div className="summary-pairs">
                  <span>平均值</span>
                  <b>{formatNumber(primaryStats.mean)}</b>
                  <span>标准差</span>
                  <b>{formatNumber(primaryStats.stdDev)}</b>
                  <span>范围</span>
                  <b>
                    {formatNumber(primaryStats.min)} –{' '}
                    {formatNumber(primaryStats.max)}
                  </b>
                </div>
              </section>

              <section className="control-section">
                <div className="section-title">
                  <span>数据处理</span>
                </div>
                <label className="field compact-field">
                  <span>移动平均窗口</span>
                  <input
                    type="number"
                    min="3"
                    max="101"
                    step="2"
                    value={smoothingWindow}
                    onChange={(event) =>
                      setSmoothingWindow(
                        Math.max(3, Number(event.target.value) || 3),
                      )
                    }
                  />
                </label>
                <div className="analysis-grid">
                  {ANALYSIS_ACTIONS.map((action) => {
                    const Icon = action.icon
                    return (
                      <button
                        type="button"
                        key={action.kind}
                        onClick={() => runAnalysis(action.kind)}
                      >
                        <Icon size={17} />
                        <span>{action.label}</span>
                      </button>
                    )
                  })}
                </div>
              </section>

              <section className="control-section">
                <div className="section-title">
                  <span>结果图层</span>
                  <small>{project.derivedSeries.length} 条</small>
                </div>
                {project.derivedSeries.length === 0 ? (
                  <p className="muted-copy">
                    处理后的曲线会叠加在原始数据上，并保留为独立图层。
                  </p>
                ) : (
                  <div className="analysis-result-list">
                    {project.derivedSeries.map((series) => (
                      <div className="analysis-result" key={series.id}>
                        <span
                          className="series-swatch"
                          style={{ backgroundColor: series.color }}
                        />
                        <span title={series.label}>{series.label}</span>
                        <button
                          className="icon-button compact"
                          type="button"
                          onClick={() => toggleDerivedSeries(series.id)}
                          title={series.visible ? '隐藏' : '显示'}
                        >
                          {series.visible ? (
                            <Eye size={14} />
                          ) : (
                            <EyeOff size={14} />
                          )}
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </section>
            </div>
          )}
        </aside>
      </main>

      {mobilePanel && (
        <button
          className="panel-backdrop mobile-only"
          type="button"
          onClick={() => setMobilePanel(null)}
          aria-label="关闭面板"
        />
      )}

      {isDragging && (
        <div className="drop-overlay">
          <Upload size={32} />
          <strong>释放文件以导入数据</strong>
          <span>CSV、TSV、TXT、JSON、RSTPLT</span>
        </div>
      )}

      {importTask?.phase === 'scan' && (
        <div
          className="rstplt-scan-backdrop"
          role="dialog"
          aria-modal="true"
          aria-labelledby="rstplt-scan-title"
        >
          <div className="rstplt-scan-card">
            <span className="rstplt-scan-icon">
              <LoaderCircle size={22} />
            </span>
            <div>
              <strong id="rstplt-scan-title">正在扫描 RSTPLT</strong>
              <span title={importTask.fileName}>{importTask.fileName}</span>
            </div>
            <div className="rstplt-progress" aria-hidden="true">
              <span
                style={{
                  width: `${Math.round(importTask.progress * 100)}%`,
                }}
              />
            </div>
            <small>
              建立变量目录 {Math.round(importTask.progress * 100)}%
            </small>
          </div>
        </div>
      )}

      {rstpltImport && (
        <RstpltImportDialog
          key={`${rstpltImport.file.name}-${rstpltImport.file.lastModified}-${rstpltImport.catalog.fileSize}`}
          catalog={rstpltImport.catalog}
          busy={importTask?.phase === 'load'}
          progress={
            importTask?.phase === 'load' ? importTask.progress : 0
          }
          onCancel={finishRstpltImport}
          onConfirm={(variableIds) => void confirmRstpltImport(variableIds)}
        />
      )}

      {notice && (
        <div className={`notice ${notice.tone}`} role="status">
          <span className="notice-mark" />
          {notice.message}
        </div>
      )}

      <input
        ref={fileInputRef}
        type="file"
        accept=".csv,.tsv,.txt,.dat,.json,.rstplt,.rstrt,text/csv,text/plain,application/json,application/octet-stream"
        multiple
        hidden
        onChange={handleFileInput}
      />
      <input
        ref={projectInputRef}
        type="file"
        accept=".json,.aptproj"
        hidden
        onChange={openProjectFile}
      />
    </div>
  )
}

export default App
