import { useEffect, useMemo, useState } from 'react'
import {
  ChartLine,
  Check,
  Database,
  Search,
  X,
} from 'lucide-react'
import { getSeriesColor } from '../lib/data'
import type { DataSet } from '../types'

export interface PlotDataSelection {
  datasetId: string
  xColumn: string
  yColumns: string[]
}

interface PlotDataDialogProps {
  datasets: DataSet[]
  initialDatasetId: string | null
  onCancel: () => void
  onConfirm: (selection: PlotDataSelection) => void
}

function numericColumnsFor(dataset: DataSet | undefined) {
  return dataset?.columns.filter((column) => column.type === 'number') ?? []
}

function initialColumns(dataset: DataSet | undefined) {
  const numericColumns = numericColumnsFor(dataset)
  const xColumn =
    numericColumns.find((column) => column.key === dataset?.xColumn)?.key ??
    numericColumns[0]?.key ??
    ''
  const savedYColumns =
    dataset?.yColumns.filter(
      (key) => key !== xColumn && numericColumns.some((column) => column.key === key),
    ) ?? []
  const fallbackY = numericColumns.find((column) => column.key !== xColumn)?.key

  return {
    xColumn,
    yColumns:
      savedYColumns.length > 0
        ? savedYColumns
        : fallbackY
          ? [fallbackY]
          : [],
  }
}

function columnPosition(dataset: DataSet, columnKey: string) {
  return dataset.columns.findIndex((column) => column.key === columnKey) + 1
}

export function PlotDataDialog({
  datasets,
  initialDatasetId,
  onCancel,
  onConfirm,
}: PlotDataDialogProps) {
  const firstDataset =
    datasets.find((dataset) => dataset.id === initialDatasetId) ?? datasets[0]
  const firstSelection = initialColumns(firstDataset)
  const [datasetQuery, setDatasetQuery] = useState('')
  const [curveQuery, setCurveQuery] = useState('')
  const [datasetId, setDatasetId] = useState(firstDataset?.id ?? '')
  const [xColumn, setXColumn] = useState(firstSelection.xColumn)
  const [yColumns, setYColumns] = useState(firstSelection.yColumns)

  const selectedDataset =
    datasets.find((dataset) => dataset.id === datasetId) ?? firstDataset
  const numericColumns = useMemo(
    () => numericColumnsFor(selectedDataset),
    [selectedDataset],
  )
  const filteredDatasets = useMemo(() => {
    const normalized = datasetQuery.trim().toLowerCase()
    if (!normalized) return datasets
    return datasets.filter(
      (dataset) =>
        dataset.name.toLowerCase().includes(normalized) ||
        dataset.fileName.toLowerCase().includes(normalized),
    )
  }, [datasets, datasetQuery])
  const curveColumns = useMemo(
    () => numericColumns.filter((column) => column.key !== xColumn),
    [numericColumns, xColumn],
  )
  const filteredCurveColumns = useMemo(() => {
    if (!selectedDataset) return []
    const normalized = curveQuery.trim().toLowerCase()
    if (!normalized) return curveColumns
    return curveColumns.filter((column) => {
      const position = String(columnPosition(selectedDataset, column.key))
      return (
        column.label.toLowerCase().includes(normalized) ||
        column.key.toLowerCase().includes(normalized) ||
        position.includes(normalized)
      )
    })
  }, [curveColumns, curveQuery, selectedDataset])

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape') onCancel()
    }
    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [onCancel])

  function chooseDataset(dataset: DataSet) {
    const nextSelection = initialColumns(dataset)
    setDatasetId(dataset.id)
    setXColumn(nextSelection.xColumn)
    setYColumns(nextSelection.yColumns)
    setCurveQuery('')
  }

  function chooseXColumn(columnKey: string) {
    const fallbackY = numericColumns.find(
      (column) => column.key !== columnKey,
    )?.key
    setXColumn(columnKey)
    setYColumns((current) => {
      const remaining = current.filter((key) => key !== columnKey)
      return remaining.length > 0
        ? remaining
        : fallbackY
          ? [fallbackY]
          : []
    })
  }

  function toggleYColumn(columnKey: string) {
    setYColumns((current) => {
      if (current.includes(columnKey)) {
        return current.length === 1
          ? current
          : current.filter((key) => key !== columnKey)
      }
      return [...current, columnKey]
    })
  }

  function restoreSavedSelection() {
    if (!selectedDataset) return
    const saved = initialColumns(selectedDataset)
    setXColumn(saved.xColumn)
    setYColumns(saved.yColumns)
  }

  function selectAllCurves() {
    setYColumns(filteredCurveColumns.map((column) => column.key))
  }

  function clearCurves() {
    setYColumns([])
  }

  if (!selectedDataset) return null

  return (
    <div
      className="rstplt-dialog-backdrop plot-data-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onCancel()
      }}
    >
      <section
        className="rstplt-dialog plot-data-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="plot-data-dialog-title"
      >
        <header className="rstplt-dialog-header">
          <span className="rstplt-dialog-icon" aria-hidden="true">
            <ChartLine size={22} />
          </span>
          <div className="rstplt-dialog-title">
            <span className="section-kicker">已导入数据</span>
            <h2 id="plot-data-dialog-title">选择绘图数据</h2>
            <p>
              <span>从已导入文件中选择横轴和曲线，不会重新读取文件</span>
            </p>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onCancel}
            title="关闭"
          >
            <X size={18} />
          </button>
        </header>

        <div className="plot-data-body">
          <aside className="plot-data-sources" aria-label="已导入文件">
            <div className="plot-data-pane-heading">
              <div>
                <strong>已导入文件</strong>
                <span>{datasets.length} 个数据源</span>
              </div>
            </div>
            <label className="plot-data-search">
              <Search size={14} />
              <input
                autoFocus
                type="search"
                value={datasetQuery}
                onChange={(event) => setDatasetQuery(event.target.value)}
                placeholder="搜索文件名"
                aria-label="搜索已导入文件"
              />
            </label>
            <div className="plot-data-source-list">
              {filteredDatasets.map((dataset) => {
                const selected = dataset.id === selectedDataset.id
                return (
                  <button
                    className={`plot-data-source ${selected ? 'selected' : ''}`}
                    key={dataset.id}
                    type="button"
                    aria-pressed={selected}
                    onClick={() => chooseDataset(dataset)}
                  >
                    <span className="plot-data-source-icon">
                      <Database size={14} />
                    </span>
                    <span className="plot-data-source-copy">
                      <strong>{dataset.name}</strong>
                      <span title={dataset.fileName}>{dataset.fileName}</span>
                      <small>
                        {dataset.rows.length.toLocaleString('zh-CN')} 行 /{' '}
                        {dataset.columns.length} 列
                      </small>
                    </span>
                    {selected && <Check size={14} />}
                  </button>
                )
              })}
              {filteredDatasets.length === 0 && (
                <div className="plot-data-source-empty">
                  <Search size={17} />
                  <span>没有匹配的数据文件</span>
                </div>
              )}
            </div>
          </aside>

          <div className="plot-data-content">
            <div className="plot-data-dataset-summary">
              <div>
                <span className="section-kicker">当前文件</span>
                <strong>{selectedDataset.name}</strong>
                <span title={selectedDataset.fileName}>
                  {selectedDataset.fileName}
                </span>
              </div>
              <dl>
                <div>
                  <dt>数据行</dt>
                  <dd>{selectedDataset.rows.length.toLocaleString('zh-CN')}</dd>
                </div>
                <div>
                  <dt>数值列</dt>
                  <dd>{numericColumns.length}</dd>
                </div>
              </dl>
            </div>

            <div className="plot-data-axis">
              <label>
                <span>横轴</span>
                <select
                  value={xColumn}
                  onChange={(event) => chooseXColumn(event.target.value)}
                >
                  {numericColumns.map((column) => (
                    <option key={column.key} value={column.key}>
                      {column.label}
                    </option>
                  ))}
                </select>
              </label>
              <p>曲线将按所选横轴的时间或数值与数据行配对。</p>
            </div>

            <div className="plot-data-curves-heading">
              <div>
                <strong>绘制曲线</strong>
                <span>至少选择 1 条</span>
              </div>
              <div className="plot-data-curve-actions">
                <button
                  className="text-button"
                  type="button"
                  onClick={restoreSavedSelection}
                >
                  恢复已选
                </button>
                <button
                  className="text-button"
                  type="button"
                  onClick={selectAllCurves}
                  disabled={filteredCurveColumns.length === 0}
                >
                  全选
                </button>
                <button
                  className="text-button"
                  type="button"
                  onClick={clearCurves}
                  disabled={yColumns.length === 0}
                >
                  清空
                </button>
              </div>
            </div>

            <label className="plot-data-curve-search">
              <Search size={14} />
              <input
                type="search"
                value={curveQuery}
                onChange={(event) => setCurveQuery(event.target.value)}
                placeholder="搜索变量名或编号"
                aria-label="搜索绘制曲线"
              />
            </label>

            <div className="plot-data-curves" role="group" aria-label="绘制曲线">
              {filteredCurveColumns.map((column) => {
                const checked = yColumns.includes(column.key)
                const position = columnPosition(selectedDataset, column.key)
                return (
                  <label
                    className={`plot-data-curve ${checked ? 'selected' : ''}`}
                    key={column.key}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleYColumn(column.key)}
                    />
                    <span
                      className="series-swatch"
                      style={{
                        backgroundColor: getSeriesColor(
                          selectedDataset,
                          column.key,
                        ),
                      }}
                    />
                    <span className="plot-data-curve-copy">
                      <strong>{column.label}</strong>
                      <small>
                        列 {position} /{' '}
                        {column.missing > 0
                          ? `${column.missing} 个缺失值`
                          : '数据完整'}
                      </small>
                    </span>
                  </label>
                )
              })}
              {curveColumns.length === 0 && (
                <div className="plot-data-curves-empty">
                  当前文件没有可用于绘制曲线的数值列。
                </div>
              )}
              {curveColumns.length > 0 &&
                filteredCurveColumns.length === 0 && (
                  <div className="plot-data-curves-empty">
                    <Search size={17} />
                    没有匹配的曲线
                  </div>
                )}
            </div>
          </div>
        </div>

        <footer className="plot-data-footer">
          <div className="plot-data-selection">
            <strong>{yColumns.length} 条曲线</strong>
            <span>
              {selectedDataset.rows.length.toLocaleString('zh-CN')} 行数据
            </span>
          </div>
          <div className="plot-data-footer-actions">
            <button className="button" type="button" onClick={onCancel}>
              取消
            </button>
            <button
              className="button button-primary"
              type="button"
              disabled={!xColumn || yColumns.length === 0}
              onClick={() =>
                onConfirm({
                  datasetId: selectedDataset.id,
                  xColumn,
                  yColumns,
                })
              }
            >
              应用选择
            </button>
          </div>
        </footer>
      </section>
    </div>
  )
}
