import { useEffect, useMemo, useState } from 'react'
import {
  CheckCheck,
  DatabaseZap,
  FileChartLine,
  ListChecks,
  LoaderCircle,
  Search,
  X,
} from 'lucide-react'
import type { LocustPlotCatalog } from '../lib/locust-plot'
import type { RstpltCatalog } from '../lib/rstplt'

export type BinaryPlotFormat = 'rstplt' | 'locust'

interface RstpltImportDialogProps {
  catalog: RstpltCatalog | LocustPlotCatalog
  format: BinaryPlotFormat
  mode?: 'import' | 'reselect'
  initialSelectedIds?: string[]
  busy: boolean
  progress: number
  onCancel: () => void
  onConfirm: (variableIds: string[]) => void
}

function formatCount(value: number) {
  return value.toLocaleString('zh-CN')
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 ** 2) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 ** 3) return `${(bytes / 1024 ** 2).toFixed(1)} MB`
  return `${(bytes / 1024 ** 3).toFixed(2)} GB`
}

function recommendedVariables(
  catalog: RstpltCatalog | LocustPlotCatalog,
  format: BinaryPlotFormat,
) {
  const plottable = catalog.variables.filter(
    (variable) => !variable.isTime,
  )
  const candidates =
    format === 'locust'
      ? plottable.filter(
          (variable) =>
            !['tstep', 'dt', 'dtcrnt'].includes(
              variable.alpha.toLowerCase(),
            ),
        )
      : plottable
  return (candidates.length > 0 ? candidates : plottable)
    .slice(0, 2)
    .map((variable) => variable.id)
}

export function RstpltImportDialog({
  catalog,
  format,
  mode = 'import',
  initialSelectedIds = [],
  busy,
  progress,
  onCancel,
  onConfirm,
}: RstpltImportDialogProps) {
  const formatName = format === 'locust' ? 'LOCUST' : 'RSTPLT'
  const formatDescription =
    format === 'locust' ? 'LOCUST 二进制数据' : 'RELAP5 二进制数据'
  const plottableVariables = useMemo(
    () => catalog.variables.filter((variable) => !variable.isTime),
    [catalog.variables],
  )
  const savedSelection = useMemo(() => {
    const available = new Set(
      plottableVariables.map((variable) => variable.id),
    )
    return initialSelectedIds.filter((variableId) =>
      available.has(variableId),
    )
  }, [initialSelectedIds, plottableVariables])
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(
    () =>
      new Set(
        mode === 'reselect' && savedSelection.length > 0
          ? savedSelection
          : recommendedVariables(catalog, format),
      ),
  )

  const filteredVariables = useMemo(() => {
    const normalized = query.trim().toLowerCase()
    if (!normalized) return plottableVariables
    return plottableVariables.filter(
      (variable) =>
        variable.code.toLowerCase().includes(normalized) ||
        String(variable.number).includes(normalized),
    )
  }, [plottableVariables, query])

  const selectedVariables = plottableVariables.filter((variable) =>
    selected.has(variable.id),
  )
  const selectedPoints = selectedVariables.reduce(
    (count, variable) => count + variable.recordCount,
    0,
  )
  const percent = Math.round(progress * 100)

  useEffect(() => {
    function handleKeydown(event: KeyboardEvent) {
      if (event.key === 'Escape' && !busy) onCancel()
    }
    window.addEventListener('keydown', handleKeydown)
    return () => window.removeEventListener('keydown', handleKeydown)
  }, [busy, onCancel])

  function toggleVariable(variableId: string) {
    setSelected((current) => {
      const next = new Set(current)
      if (next.has(variableId)) next.delete(variableId)
      else next.add(variableId)
      return next
    })
  }

  return (
    <div
      className="rstplt-dialog-backdrop"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !busy) onCancel()
      }}
    >
      <section
        className="rstplt-dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="rstplt-dialog-title"
      >
        <header className="rstplt-dialog-header">
          <span className="rstplt-dialog-icon" aria-hidden="true">
            <FileChartLine size={22} />
          </span>
          <div className="rstplt-dialog-title">
            <span className="section-kicker">
              {mode === 'reselect' ? '已导入数据' : formatDescription}
            </span>
            <h2 id="rstplt-dialog-title">
              {mode === 'reselect'
                ? `重新选择 ${formatName} 曲线`
                : `选择 ${formatName} 曲线`}
            </h2>
            <p title={catalog.fileName}>
              <span>{catalog.fileName}</span>
              <span>{formatFileSize(catalog.fileSize)}</span>
            </p>
          </div>
          <button
            className="icon-button"
            type="button"
            onClick={onCancel}
            title="关闭"
            disabled={busy}
          >
            <X size={18} />
          </button>
        </header>

        <div className="rstplt-summary">
          <div>
            <span>变量</span>
            <strong>{formatCount(plottableVariables.length)}</strong>
          </div>
          <div>
            <span>时间记录</span>
            <strong>{formatCount(catalog.recordCount)}</strong>
          </div>
          <div>
            <span>{format === 'locust' ? '数据段' : '重启段'}</span>
            <strong>{formatCount(catalog.segments.length)}</strong>
          </div>
          <div className="rstplt-summary-wide">
            <span>文件信息</span>
            <strong title={`${catalog.programId} ${catalog.fileDate}`}>
              {catalog.programId || formatName}
              <small>{catalog.problemTypeName}</small>
            </strong>
          </div>
        </div>

        <div className="rstplt-toolbar">
          <label className="rstplt-search">
            <Search size={15} />
            <input
              autoFocus
              type="search"
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索变量名或编号"
              aria-label={`搜索 ${formatName} 变量`}
              disabled={busy}
            />
          </label>
          <div className="rstplt-toolbar-actions">
            <button
              className="button button-quiet"
              type="button"
              onClick={() => {
                setSelected(
                  new Set(
                    mode === 'reselect' && savedSelection.length > 0
                      ? savedSelection
                      : recommendedVariables(catalog, format),
                  ),
                )
              }}
              disabled={
                busy ||
                (mode === 'reselect' && savedSelection.length === 0)
              }
            >
              <ListChecks size={14} />
              {mode === 'reselect' ? '恢复已选' : '推荐'}
            </button>
            <button
              className="button button-quiet"
              type="button"
              onClick={() =>
                setSelected(new Set(plottableVariables.map((variable) => variable.id)))
              }
              disabled={busy}
            >
              <CheckCheck size={14} />
              全选
            </button>
            <button
              className="button button-quiet"
              type="button"
              onClick={() => setSelected(new Set())}
              disabled={busy || selected.size === 0}
            >
              清空
            </button>
          </div>
        </div>

        <div className="rstplt-variable-region">
          <div className="rstplt-axis-note">
            <DatabaseZap size={15} />
            <span>
              横轴使用 plotrec 内的 <code>time</code>，曲线按
              {format === 'locust' ? '数据段' : '重启段'}自动合并
            </span>
          </div>

          <div
            className="rstplt-variable-list"
            role="group"
            aria-label={`${formatName} 变量`}
          >
            {filteredVariables.map((variable) => {
              const checked = selected.has(variable.id)
              return (
                <label
                  className={`rstplt-variable ${checked ? 'selected' : ''}`}
                  key={variable.id}
                >
                  <input
                    type="checkbox"
                    checked={checked}
                    onChange={() => toggleVariable(variable.id)}
                    disabled={busy}
                  />
                  <span className="rstplt-variable-mark" aria-hidden="true" />
                  <span className="rstplt-variable-copy">
                    <strong>{variable.code}</strong>
                    <small>
                      #{variable.number} / {variable.occurrences.length} 段 /{' '}
                      {formatCount(variable.recordCount)} 点
                    </small>
                  </span>
                </label>
              )
            })}
            {filteredVariables.length === 0 && (
              <div className="rstplt-empty">
                <Search size={20} />
                <span>没有匹配的变量</span>
              </div>
            )}
          </div>
        </div>

        <footer className="rstplt-dialog-footer">
          <div className="rstplt-selection">
            <strong>{selectedVariables.length} 条曲线</strong>
            <span>
              {formatCount(selectedPoints)} 个数据点，仅读取所选变量
            </span>
            {selectedVariables.length > 20 && (
              <small>曲线较多，导入后会占用更多浏览器内存。</small>
            )}
          </div>
          {busy ? (
            <div className="rstplt-loading" role="status" aria-live="polite">
              <span>
                <LoaderCircle size={15} />
                正在读取 {percent}%
              </span>
              <div className="rstplt-progress" aria-hidden="true">
                <span style={{ width: `${percent}%` }} />
              </div>
            </div>
          ) : (
            <div className="rstplt-footer-actions">
              <button className="button" type="button" onClick={onCancel}>
                取消
              </button>
              <button
                className="button button-primary"
                type="button"
                disabled={selectedVariables.length === 0}
                onClick={() =>
                  onConfirm(selectedVariables.map((variable) => variable.id))
                }
              >
                {mode === 'reselect' ? '更新绘图' : '导入'}{' '}
                {selectedVariables.length} 条曲线
              </button>
            </div>
          )}
        </footer>
      </section>
    </div>
  )
}
